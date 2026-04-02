"""
Chunk embedding + Doc2Query question generation at ingestion time.

embed_and_index: called from a background task after chunks are persisted to SQLite.
  - Calls upsert_chunk(chunk_id, text, ...) — LangChain Chroma embeds via text-embedding-004
  - If enable_doc2query: generates N hypothetical questions per chunk via LangChain LCEL,
    then calls upsert_questions(chunk_id, questions, ...) — Chroma embeds each question
All Gemini API calls degrade gracefully — if unreachable the chunk is simply skipped.
"""
from __future__ import annotations

import logging

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI

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


def _generate_questions(chunk_text: str, n: int) -> list[str]:
    """Generate n hypothetical questions for a chunk via LangChain LCEL."""
    try:
        chain = _DOC2QUERY_PROMPT | ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            google_api_key=settings.google_api_key,
            timeout=30,
        ) | StrOutputParser()
        raw = chain.invoke({"n": n, "chunk_text": chunk_text[:800]}).strip()
        return [q.strip() for q in raw.splitlines() if q.strip()][:n]
    except Exception:
        return []


def embed_and_index(
    chunks: list[ChunkRecord],
    chunk_ids: list[str],
    artifact_id: str,
    user_id: str,
    filename: str,
    figures_b64: list[str] | None = None,
) -> int:
    """
    Upsert chunks into ChromaDB via LangChain Chroma (embedding handled internally).
    chunk_ids must be parallel to chunks (same order, same length).

    figures_b64 is accepted for API compatibility but image description is not
    supported in the cloud deployment (enable_image_description defaults to False).

    Skips gracefully if the Gemini API is unreachable.
    Returns the number of chunks successfully embedded (0 on API failure).
    """
    if not settings.enable_embeddings:
        return 0

    success = 0

    for chunk, chunk_id in zip(chunks, chunk_ids):
        try:
            upsert_chunk(chunk_id, chunk.text, artifact_id, user_id, filename)
            success += 1
        except Exception:
            continue  # API unreachable — skip this chunk

        # Doc2Query: generate hypothetical questions and index them
        if settings.enable_doc2query:
            questions = _generate_questions(chunk.text, settings.doc2query_questions)
            if questions:
                try:
                    upsert_questions(chunk_id, questions, artifact_id, user_id, filename)
                except Exception:
                    pass

    return success
