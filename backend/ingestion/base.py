"""
Base types for the ingestion layer.

Every parser transforms raw file bytes into a ParseResult — the normalized
intermediate representation consumed by the chunking layer downstream.

PDF/DOCX:  parsed via Docling → markdown_content (clean markdown string)
XLSX:      parsed via pandas  → xlsx_rows (list of structured row dicts)
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ParseResult:
    """Fully normalized output of a file-type parser."""
    filename: str
    file_type: str                      # "pdf" | "docx" | "xlsx"
    size_bytes: int
    file_hash: str                      # SHA-256 hex (computed before parsing, passed in)
    extracted_metadata: dict            # Filetype-specific intrinsic metadata

    # PDF / DOCX: full Docling markdown output
    markdown_content: str = ""

    # XLSX: one dict per data row — [{sheet, row_index, text, headers}]
    # text is "Col: Val | Col: Val" formatted; used by XlsxChunker for provenance
    xlsx_rows: list[dict] = field(default_factory=list)


class BaseParser(ABC):
    @abstractmethod
    def parse(self, path: Path, file_hash: str, size_bytes: int) -> ParseResult:
        """Parse a file and return a normalized ParseResult."""
        ...
