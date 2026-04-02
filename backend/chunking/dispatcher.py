"""
Chunking dispatcher — routes a ParseResult to the appropriate chunker by file type.

Supported file types and their chunkers:
  pdf   → MarkdownChunker  (heading-aware, uses LangChain RCTS)
  docx  → MarkdownChunker  (same pipeline; Docling outputs markdown for both)
  xlsx  → XlsxChunker      (row-window chunker with sheet/row provenance)

Chunker singletons are created once at import time to avoid re-instantiating
the LangChain splitter configuration on every upload.
"""
from __future__ import annotations

from chunking.base import BaseChunker, ChunkRecord
from chunking.markdown_chunker import MarkdownChunker
from chunking.xlsx_chunker import XlsxChunker
from ingestion.base import ParseResult

# One chunker instance per file type — singletons to avoid repeated setup
_CHUNKERS: dict[str, BaseChunker] = {
    "pdf": MarkdownChunker(),
    "docx": MarkdownChunker(),
    "xlsx": XlsxChunker(),
}


def chunk_result(result: ParseResult) -> list[ChunkRecord]:
    """
    Split a ParseResult into a list of ChunkRecord objects.

    Delegates to the chunker registered for ``result.file_type``.

    Args:
        result: Normalised parser output from ingestion/dispatcher.py.

    Returns:
        Ordered list of ChunkRecord objects ready for contextual enrichment
        and persistence.

    Raises:
        ValueError: if result.file_type has no registered chunker.
    """
    chunker = _CHUNKERS.get(result.file_type)
    if chunker is None:
        raise ValueError(f"No chunker registered for file_type: {result.file_type!r}")
    return chunker.chunk(result)
