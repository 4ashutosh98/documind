"""
LangChain-backed vector store for semantic chunk storage.

Two Chroma collections (via langchain-chroma + langchain-google-genai):
  - "chunks"    — one vector per chunk text
  - "questions" — N vectors per chunk for Doc2Query hypothetical questions
    Both store chunk_id + artifact_id + user_id in metadata.

Embedding is handled internally by GoogleGenerativeAIEmbeddings (text-embedding-004, 768-dim).
Callers pass plain text; this module handles embed + upsert transparently.
"""
from __future__ import annotations

from langchain_chroma import Chroma
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from config import settings

# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

_chunks_store: Chroma | None = None
_questions_store: Chroma | None = None


def _embeddings() -> GoogleGenerativeAIEmbeddings:
    return GoogleGenerativeAIEmbeddings(
        model=settings.gemini_embed_model,
        google_api_key=settings.google_api_key,
    )


def _get_chunks_store() -> Chroma:
    global _chunks_store
    if _chunks_store is None:
        _chunks_store = Chroma(
            collection_name="chunks",
            embedding_function=_embeddings(),
            persist_directory=settings.chroma_dir,
            collection_metadata={"hnsw:space": "cosine"},
        )
    return _chunks_store


def _get_questions_store() -> Chroma:
    global _questions_store
    if _questions_store is None:
        _questions_store = Chroma(
            collection_name="questions",
            embedding_function=_embeddings(),
            persist_directory=settings.chroma_dir,
            collection_metadata={"hnsw:space": "cosine"},
        )
    return _questions_store


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def upsert_chunk(
    chunk_id: str,
    text: str,
    artifact_id: str,
    user_id: str,
    filename: str,
) -> None:
    """Embed `text` via Gemini and upsert into the chunks collection."""
    _get_chunks_store().add_texts(
        texts=[text],
        metadatas=[{
            "chunk_id": chunk_id,
            "artifact_id": artifact_id,
            "user_id": user_id,
            "filename": filename,
        }],
        ids=[chunk_id],
    )


def upsert_questions(
    chunk_id: str,
    questions: list[str],
    artifact_id: str,
    user_id: str,
    filename: str,
) -> None:
    """Embed Doc2Query questions and upsert into the questions collection."""
    if not questions:
        return
    ids = [f"{chunk_id}__q{i}" for i in range(len(questions))]
    metadatas = [
        {"chunk_id": chunk_id, "artifact_id": artifact_id, "user_id": user_id, "filename": filename}
        for _ in questions
    ]
    _get_questions_store().add_texts(texts=questions, metadatas=metadatas, ids=ids)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def query_chunks(
    q: str,
    user_id: str,
    artifact_ids: list[str] | None,
    k: int = 10,
) -> list[tuple[str, float]]:
    """
    Embed query string and search the chunks collection.
    Returns [(chunk_id, cosine_distance)] sorted by distance (lower = more similar).
    """
    store = _get_chunks_store()
    count = store._collection.count()
    if count == 0:
        return []

    # ChromaDB 0.5+ requires $and for compound filters mixing equality + operator
    if artifact_ids:
        where: dict = {"$and": [{"user_id": user_id}, {"artifact_id": {"$in": artifact_ids}}]}
    else:
        where = {"user_id": user_id}

    try:
        # similarity_search_with_score returns (Document, distance) — distance for cosine space
        results = store.similarity_search_with_score(q, k=min(k, count), filter=where)
        return [(doc.metadata["chunk_id"], dist) for doc, dist in results]
    except Exception:
        return []


def query_questions(
    q: str,
    user_id: str,
    artifact_ids: list[str] | None,
    k: int = 10,
) -> list[tuple[str, float]]:
    """
    Embed query string and search the Doc2Query questions collection.
    Returns [(chunk_id, cosine_distance)] — the parent chunk_id, not the question vector id.
    """
    store = _get_questions_store()
    count = store._collection.count()
    if count == 0:
        return []

    # ChromaDB 0.5+ requires $and for compound filters mixing equality + operator
    if artifact_ids:
        where: dict = {"$and": [{"user_id": user_id}, {"artifact_id": {"$in": artifact_ids}}]}
    else:
        where = {"user_id": user_id}

    try:
        results = store.similarity_search_with_score(q, k=min(k, count), filter=where)
        return [(doc.metadata["chunk_id"], dist) for doc, dist in results]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Copy (dedup fast path)
# ---------------------------------------------------------------------------

def copy_chunk_embeddings(
    source_chunk_ids: list[str],
    target_chunk_ids: list[str],
    target_artifact_id: str,
    target_user_id: str,
    target_filename: str,
) -> int:
    """
    Copy vectors from source chunks to target chunks without calling the embedding API.

    Used when a second user uploads the same file — the blob and chunks are
    already stored, and the source user's embeddings already exist in Chroma.
    We read the raw vectors and upsert them under new IDs with the target
    user's metadata. Zero API calls needed.

    Returns the number of chunks copied.
    Returns 0 if the source chunks are not yet indexed (caller should fall
    back to embed_and_index so the target still gets indexed eventually).
    """
    if not source_chunk_ids or len(source_chunk_ids) != len(target_chunk_ids):
        return 0

    id_map = dict(zip(source_chunk_ids, target_chunk_ids))
    chunks_store = _get_chunks_store()

    try:
        result = chunks_store._collection.get(
            ids=source_chunk_ids,
            include=["embeddings", "documents", "metadatas"],
        )
    except Exception:
        return 0

    found_ids: list[str] = result.get("ids") or []
    if not found_ids:
        return 0  # source not yet indexed — caller should fall back

    # Use explicit None checks — Chroma returns NumPy arrays for embeddings,
    # and `array or []` raises "truth value of array is ambiguous"
    embeddings = result.get("embeddings")
    if embeddings is None:
        embeddings = []
    documents = result.get("documents") or []
    new_ids = [id_map[sid] for sid in found_ids]
    new_metadatas = [
        {
            "chunk_id": id_map[sid],
            "artifact_id": target_artifact_id,
            "user_id": target_user_id,
            "filename": target_filename,
        }
        for sid in found_ids
    ]

    try:
        chunks_store._collection.upsert(
            ids=new_ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=new_metadatas,
        )
    except Exception:
        return 0

    # Copy Doc2Query question vectors (non-fatal if it fails)
    if settings.enable_doc2query:
        try:
            q_store = _get_questions_store()
            q_result = q_store._collection.get(
                where={"chunk_id": {"$in": found_ids}},
                include=["embeddings", "documents", "metadatas"],
            )
            q_ids: list[str] = q_result.get("ids") or []
            if q_ids:
                q_embeddings = q_result.get("embeddings")
                if q_embeddings is None:
                    q_embeddings = []
                q_documents = q_result.get("documents") or []
                q_metadatas: list[dict] = q_result.get("metadatas") or []
                new_q_ids = []
                new_q_metas = []
                for i, qid in enumerate(q_ids):
                    src_cid = q_metadatas[i].get("chunk_id", "")
                    tgt_cid = id_map.get(src_cid, src_cid)
                    new_q_ids.append(qid.replace(src_cid, tgt_cid, 1))
                    new_q_metas.append({
                        "chunk_id": tgt_cid,
                        "artifact_id": target_artifact_id,
                        "user_id": target_user_id,
                        "filename": target_filename,
                    })
                q_store._collection.upsert(
                    ids=new_q_ids,
                    embeddings=q_embeddings,
                    documents=q_documents,
                    metadatas=new_q_metas,
                )
        except Exception:
            pass  # question copy failure is non-fatal

    return len(found_ids)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete_artifact_vectors(artifact_id: str) -> None:
    """Remove all vectors for an artifact (called on artifact deletion)."""
    for get_store in [_get_chunks_store, _get_questions_store]:
        try:
            get_store()._collection.delete(where={"artifact_id": artifact_id})
        except Exception:
            pass
