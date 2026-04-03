"""
Contextual enrichment via LangChain LCEL (Anthropic Contextual Retrieval pattern).

For each chunk a local LLM generates 1-2 sentences that situate the chunk within
its source document. This context is prepended so that when the chunk is retrieved
in isolation it still carries meaning:

  [Metadata] filename: report.pdf | type: pdf | section: Financial Highlights
  [Context]  This chunk discusses Nvidia's Q3 2024 revenue of $18.1B,
             representing 122% year-over-year growth, from their quarterly 10-Q.
  [Content]  Revenue grew 122% year over year to $18.1 billion...

Chain: PromptTemplate | ChatGroq | StrOutputParser  (via langchain-groq)
Concurrency: all chunks are enriched in parallel via asyncio.gather() so the
total enrichment time is max(single_call_time) rather than sum(all_call_times).
This makes upload latency independent of document length.
Graceful degradation: if the Groq API is unreachable or rate-limited, chunks are returned unchanged.
"""
from __future__ import annotations

import asyncio
import logging

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_groq import ChatGroq

from chunking.base import ChunkRecord
from config import settings

_log = logging.getLogger(__name__)

_ENRICH_PROMPT = PromptTemplate.from_template(
    "You are a document analysis assistant helping build a retrieval system.\n\n"
    "Given a document and one chunk extracted from it, write 1-2 sentences that "
    "situate this chunk within the broader document. Be specific and factual. "
    "Only output the context sentences — no preamble, no \"This chunk discusses\".\n\n"
    "Document: {filename} ({file_type})\n"
    "Document beginning:\n{doc_start}\n\n"
    "Chunk to contextualize:\n{chunk_text}\n\n"
    "Context:"
)


def _build_metadata_line(chunk: ChunkRecord, filename: str, file_type: str) -> str:
    parts = [f"filename: {filename}", f"type: {file_type}"]
    prov = chunk.provenance
    if prov.get("section"):
        parts.append(f"section: {prov['section']}")
    if prov.get("breadcrumb"):
        parts.append(f"breadcrumb: {prov['breadcrumb']}")
    if prov.get("sheet"):
        parts.append(f"sheet: {prov['sheet']}")
    return " | ".join(parts)


async def enrich_chunks(
    chunks: list[ChunkRecord],
    filename: str,
    file_type: str,
    doc_start: str,
    groq_api_key: str = "",
) -> list[ChunkRecord]:
    """
    Enrich all chunks concurrently with Groq-generated context.

    All chunks are enriched in parallel via asyncio.gather() — the upload
    response time is bounded by the slowest single call rather than
    the sum of all calls (N × call_time for N chunks).

    Returns chunks unchanged if enrichment is disabled or Groq is unreachable.
    groq_api_key: if provided (e.g. from user's own key via request header),
                  used instead of the server's configured key.
    """
    if not settings.enable_contextual_enrichment:
        _log.info("[enricher] contextual enrichment disabled — skipping %d chunks", len(chunks))
        return chunks

    _log.info("[enricher] enriching %d chunks for %s", len(chunks), filename)
    resolved_key = groq_api_key or settings.groq_api_key
    chain = _ENRICH_PROMPT | ChatGroq(
        model=settings.groq_model,
        groq_api_key=resolved_key,
    ) | StrOutputParser()

    succeeded = 0

    async def _enrich_one(chunk: ChunkRecord) -> ChunkRecord:
        """Enrich a single chunk; returns it unchanged on any failure."""
        nonlocal succeeded
        try:
            context = await chain.ainvoke({
                "filename": filename,
                "file_type": file_type.upper(),
                "doc_start": doc_start[:600],
                "chunk_text": chunk.text[:800],
            })
            context = context.strip()
            if context:
                metadata_line = _build_metadata_line(chunk, filename, file_type)
                chunk.text = f"[Metadata] {metadata_line}\n[Context] {context}\n[Content] {chunk.text}"
                chunk.token_count = len(chunk.text.split())
                succeeded += 1
        except Exception as exc:
            _log.warning("[enricher] chunk %d failed: %s", chunk.chunk_index, exc)
        return chunk

    # Fire all enrichment calls concurrently; order is preserved by gather()
    result = list(await asyncio.gather(*[_enrich_one(c) for c in chunks]))
    _log.info("[enricher] enrichment complete: %d/%d chunks succeeded", succeeded, len(chunks))
    return result
