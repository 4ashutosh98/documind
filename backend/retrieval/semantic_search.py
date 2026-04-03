"""
Semantic search: query string → LangChain Chroma → fetch full rows from SQLite.

The LangChain Chroma store (vector_store.py) handles embedding internally via
OllamaEmbeddings; callers never touch raw vectors here.

Returns the same dict format as keyword_search.search() so hybrid_search.py
can merge results from both sources without any downstream changes.

Similarity threshold: ChromaDB always returns k results regardless of relevance.
Without a threshold, completely unrelated chunks enter RRF and pollute results.
_DISTANCE_THRESHOLD filters out chunks whose cosine distance exceeds the cutoff.
With nomic-embed-text (normalized 768-dim vectors):
  < 0.2 → highly similar (same topic)
  0.2–0.4 → related
  > 0.5 → likely unrelated — excluded
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from storage.vector_store import query_chunks, query_questions

# Only include chunks whose cosine distance is below this value.
# Cosine distance with nomic-embed-text: 0 = identical, 1 = orthogonal.
# Chunks above 0.5 are not meaningfully similar to the query.
_DISTANCE_THRESHOLD = 0.5


def _fetch_rows(db: Session, chunk_ids: list[str], user_id: str) -> dict[str, dict]:
    """Fetch full chunk + artifact rows from SQLite for a list of chunk_ids."""
    if not chunk_ids:
        return {}
    placeholders = ", ".join(f":cid_{i}" for i in range(len(chunk_ids)))
    params = {f"cid_{i}": cid for i, cid in enumerate(chunk_ids)}
    params["user_id"] = user_id

    sql = text(f"""
        SELECT
            c.id            AS chunk_id,
            c.artifact_id,
            c.chunk_index,
            c.text          AS chunk_text,
            c.chunk_type,
            c.provenance,
            c.token_count,
            a.id            AS artifact_id,
            a.user_id,
            a.filename,
            a.file_type,
            a.size_bytes,
            a.file_hash,
            a.version_number,
            a.parent_id,
            a.uploaded_by,
            a.upload_timestamp,
            a.first_seen,
            a.last_seen,
            a.extracted_metadata,
            a.embedding_status
        FROM chunks c
        JOIN artifacts a ON c.artifact_id = a.id
        WHERE c.id IN ({placeholders})
          AND a.user_id = :user_id
    """)
    rows = db.execute(sql, params).mappings().all()
    return {row["chunk_id"]: dict(row) for row in rows}


def search(
    db: Session,
    q: str,
    user_id: str,
    artifact_ids: list[str] | None = None,
    limit: int = 10,
    google_api_key: str = "",
) -> list[dict]:
    """
    Semantic search via ChromaDB + Gemini embeddings.
    Returns [] if the embedding API is unreachable or no vectors exist.
    google_api_key: user-provided key (overrides server key if set).
    """
    chunk_hits = query_chunks(q, user_id, artifact_ids, k=limit, google_api_key=google_api_key)
    question_hits = query_questions(q, user_id, artifact_ids, k=limit, google_api_key=google_api_key)

    # Best cosine distance per chunk_id (lower = more similar)
    best: dict[str, float] = {}
    for chunk_id, dist in chunk_hits + question_hits:
        if chunk_id not in best or dist < best[chunk_id]:
            best[chunk_id] = dist

    ranked = [
        (cid, dist)
        for cid, dist in sorted(best.items(), key=lambda x: x[1])
        if dist < _DISTANCE_THRESHOLD
    ][:limit]
    if not ranked:
        return []

    chunk_ids = [cid for cid, _ in ranked]
    rows_by_id = _fetch_rows(db, chunk_ids, user_id)

    results = []
    for chunk_id, dist in ranked:
        row = rows_by_id.get(chunk_id)
        if row:
            results.append({
                **row,
                "score": round(1.0 - dist, 4),  # cosine similarity
                "match_positions": [],
            })
    return results
