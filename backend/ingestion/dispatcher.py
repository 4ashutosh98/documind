"""
Ingestion dispatcher — routes a file path to the correct parser by extension.

Supported extensions and their parsers:
  .pdf   → DoclingParser  (IBM Docling, layout-aware)
  .docx  → DoclingParser  (same pipeline handles both PDF and DOCX)
  .xlsx  → XlsxParser     (pandas-based tabular parser)

Parser singletons are created once at module import time so Docling's AI
models are only loaded once, not on every upload request.
"""
from __future__ import annotations
from pathlib import Path

from ingestion.base import BaseParser, ParseResult
from ingestion.docling_parser import DoclingParser
from ingestion.xlsx_parser import XlsxParser

# Module-level parser singletons — prevents repeated AI model initialisation
_docling = DoclingParser()
_xlsx = XlsxParser()

# Map file extension → parser instance.
# DoclingParser handles both .pdf and .docx via the same Docling pipeline.
_PARSERS: dict[str, BaseParser] = {
    ".pdf": _docling,
    ".docx": _docling,   # Docling handles both PDF and DOCX natively
    ".xlsx": _xlsx,
}

# Expose the set of supported extensions for input validation in the upload endpoint.
SUPPORTED_EXTENSIONS = set(_PARSERS.keys())


def parse_file(path: Path, file_hash: str, size_bytes: int) -> ParseResult:
    """
    Parse a file using the appropriate parser for its extension.

    Args:
        path:       Absolute path to the file on disk.
        file_hash:  SHA-256 hex digest, pre-computed before any disk write.
                    Passed through to the parser so the ParseResult carries it.
        size_bytes: Raw file size in bytes, passed through to ParseResult.

    Returns:
        ParseResult: normalised intermediate representation containing:
          - filename, file_type, size_bytes, file_hash
          - extracted_metadata (title, page_count, sheet_names, etc.)
          - markdown_content (PDF/DOCX) or xlsx_rows (XLSX)

    Raises:
        ValueError: if the file extension is not in SUPPORTED_EXTENSIONS.
    """
    ext = path.suffix.lower()
    parser = _PARSERS.get(ext)
    if parser is None:
        raise ValueError(
            f"Unsupported file type: {ext!r}. Supported: {sorted(SUPPORTED_EXTENSIONS)}"
        )
    return parser.parse(path, file_hash, size_bytes)
