"""
Contextual enrichment via LangChain LCEL (Anthropic Contextual Retrieval pattern).

For each chunk a local LLM generates 1-2 sentences that situate the chunk within
its source document. This context is prepended so that when the chunk is retrieved
in isolation it still carries meaning:

  [Metadata] filename: report.pdf | type: pdf | section: Financial Highlights
  [Context]  This chunk discusses Nvidia's Q3 2024 revenue of $18.1B,
             representing 122% year-over-year growth, from their quarterly 10-Q.
  [Content]  Revenue grew 122% year over year to $18.1 billion...

Chain: PromptTemplate | ChatGoogleGenerativeAI | StrOutputParser  (via langchain-google-genai)
Concurrency: all chunks are enriched in parallel via asyncio.gather() so the
total enrichment time is max(single_call_time) rather than sum(all_call_times).
This makes upload latency independent of document length.
Graceful degradation: if the Gemini API is unreachable or rate-limited, chunks are returned unchanged.
"""
from __future__ import annotations

import asyncio

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI

from chunking.base import ChunkRecord
from config import settings

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
) -> list[ChunkRecord]:
    """
    Enrich all chunks concurrently with Gemini-generated context.

    All chunks are enriched in parallel via asyncio.gather() — the upload
    response time is bounded by the slowest single call (~30s) rather than
    the sum of all calls (N × 30s for N chunks).

    Returns chunks unchanged if enrichment is disabled or Ollama is unreachable.
    """
    if not settings.enable_contextual_enrichment:
        return chunks

    chain = _ENRICH_PROMPT | ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.google_api_key,
        timeout=30,
    ) | StrOutputParser()

    async def _enrich_one(chunk: ChunkRecord) -> ChunkRecord:
        """Enrich a single chunk; returns it unchanged on any failure."""
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
        except Exception:
            pass  # Gracefully degrade — enrichment is an enhancement, not required
        return chunk

    # Fire all enrichment calls concurrently; order is preserved by gather()
    return list(await asyncio.gather(*[_enrich_one(c) for c in chunks]))
