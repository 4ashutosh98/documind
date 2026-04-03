"""
Chunk embedding + Doc2Query question generation at ingestion time.

embed_and_index: called from a background task after chunks are persisted to SQLite.
  - Calls upsert_chunk(chunk_id, text, ...) — LangChain Chroma embeds via text-embedding-004
  - If enable_doc2query: generates N hypothetical questions per chunk via LangChain LCEL (Groq),
    then calls upsert_questions(chunk_id, questions, ...) — Chroma embeds each question
All API calls degrade gracefully — if unreachable the chunk is simply skipped.
"""
from __future__ import annotations

import logging

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_groq import ChatGroq

from chunking.base import ChunkRecord
from config import settings
from storage.vector_store import upsert_chunk, upsert_questions

_log = logging.getLogger(__name__)

_DOC2QUERY_PROMPT = PromptTemplate.from_template(
    "You are building a search index. Given a document chunk, generate {n} short, "
    "specific questions that a user might ask whose answer is contained in this chunk. "
    "Output ONLY the questions, one per line, no numbering, no preamble.\n\n"
    "Chunk:\n{chunk_text}\n\n"
    "Questions:"
)


def _generate_questions(chunk_text: str, n: int, groq_api_key: str = "") -> list[str]:
    """Generate n hypothetical questions for a chunk via LangChain LCEL (Groq)."""
    try:
        resolved_key = groq_api_key or settings.groq_api_key
        chain = _DOC2QUERY_PROMPT | ChatGroq(
            model=settings.groq_model,
            groq_api_key=resolved_key,
        ) | StrOutputParser()
        raw = chain.invoke({"n": n, "chunk_text": chunk_text[:800]}).strip()
        questions = [q.strip() for q in raw.splitlines() if q.strip()][:n]
        return questions
    except Exception as exc:
        _log.warning("[embedder] doc2query generation failed: %s", exc)
        return []


def embed_and_index(
    chunks: list[ChunkRecord],
    chunk_ids: list[str],
    artifact_id: str,
    user_id: str,
    filename: str,
    figures_b64: list[str] | None = None,
    groq_api_key: str = "",
    google_api_key: str = "",
) -> int:
    """
    Upsert chunks into ChromaDB via LangChain Chroma (embedding handled internally).
    chunk_ids must be parallel to chunks (same order, same length).

    figures_b64 is accepted for API compatibility but image description is not
    supported in the cloud deployment (enable_image_description defaults to False).

    Skips gracefully if the embedding API is unreachable.
    Returns the number of chunks successfully embedded (0 on API failure).
    groq_api_key: user-provided Groq key for doc2query generation (overrides server key if set).
    google_api_key: user-provided Gemini key for embeddings (overrides server key if set).
    """
    if not settings.enable_embeddings:
        _log.info("[embedder] embeddings disabled — skipping artifact %s", artifact_id)
        return 0

    _log.info("[embedder] embedding %d chunks for artifact %s (%s)", len(chunks), artifact_id, filename)
    success = 0

    for chunk, chunk_id in zip(chunks, chunk_ids):
        try:
            upsert_chunk(chunk_id, chunk.text, artifact_id, user_id, filename, google_api_key)
            success += 1
            _log.info("[embedder] embedded chunk %d/%d (id=%s)", success, len(chunks), chunk_id)
        except Exception as exc:
            _log.warning("[embedder] failed to embed chunk %s: %s", chunk_id, exc)
            continue

        # Doc2Query: generate hypothetical questions and index them
        if settings.enable_doc2query:
            questions = _generate_questions(chunk.text, settings.doc2query_questions, groq_api_key)
            if questions:
                _log.info("[embedder] doc2query: %d questions generated for chunk %s", len(questions), chunk_id)
                try:
                    upsert_questions(chunk_id, questions, artifact_id, user_id, filename, google_api_key)
                except Exception as exc:
                    _log.warning("[embedder] failed to index questions for chunk %s: %s", chunk_id, exc)

    _log.info("[embedder] complete: %d/%d chunks embedded for %s", success, len(chunks), filename)
    return success
