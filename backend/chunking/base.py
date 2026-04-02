"""
Base types for the chunking layer.

All chunkers take a ParseResult and produce a list of ChunkRecord objects.
Each ChunkRecord carries the text content, its position within the artifact,
and a provenance dict so downstream systems can trace results back to their
exact source location.

Token counting
--------------
count_tokens uses whitespace splitting rather than a proper BPE tokeniser.
This is ~10 % of the actual BPE count for English prose, but it is:
  - Zero-dependency (no tiktoken/transformers required)
  - Fast enough for chunking on the upload critical path
  - Consistent: used in both the chunker (for chunk_size enforcement)
    and stored on each Chunk row (for display in the UI)
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ingestion.base import ParseResult


@dataclass
class ChunkRecord:
    """
    A single retrieval-sized piece of document content with full provenance.

    Produced by a BaseChunker and consumed by:
      - chunk_store.bulk_insert  → writes to the ``chunks`` SQLite table
      - embed_and_index          → embeds into ChromaDB
      - enrich_chunks            → optionally prepends Ollama-generated context

    Attributes
    ----------
    text : str
        The chunk content.  After contextual enrichment this may be prefixed
        with ``[Metadata] … [Context] … [Content]`` lines.
    chunk_index : int
        Zero-based sequential position of this chunk within the artifact.
    chunk_type : str
        One of ``"text"``, ``"heading"``, ``"table_row"``.
        Used by the UI to colour-code chunk badges.
    provenance : dict
        Source location.  Schema varies by file type — see models/chunk.py.
    token_count : int | None
        Estimated token count (whitespace-split).  Stored on the DB row and
        summed for display in the artifact detail modal.
    """
    text: str
    chunk_index: int
    chunk_type: str = "text"        # "text" | "table_row" | "heading"
    provenance: dict = field(default_factory=dict)
    token_count: int | None = None


class BaseChunker(ABC):
    """Abstract interface that every chunker must implement."""

    @abstractmethod
    def chunk(self, result: ParseResult) -> list[ChunkRecord]:
        """
        Split a ParseResult into ChunkRecord objects.

        Args:
            result: Normalised parser output.  Chunkers should read
                    result.markdown_content (PDF/DOCX) or result.xlsx_rows
                    (XLSX), ignoring fields that don't apply to their type.

        Returns:
            Ordered list of ChunkRecord objects (may be empty if the document
            has no extractable content).
        """
        ...


def count_tokens(text: str) -> int:
    """
    Estimate token count by splitting on whitespace.

    This is ~10 % of true BPE count for English prose — fast and zero-dependency.
    Suitable for soft size limits on chunk_size and for display purposes.

    Args:
        text: Any string.

    Returns:
        Number of whitespace-separated tokens (words).
    """
    return len(text.split())
