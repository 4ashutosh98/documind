"""
XLSX parser using pandas.

Spreadsheets are structured data, not documents — Docling is the wrong tool
here. Pandas gives us full access to the tabular structure.

Each data row is converted to a "Col: Val | Col: Val" string that is both
human-readable and embeds well. The column headers (from row 0) become the
keys, so every row chunk is self-describing.

Statistical metadata (shape, column types, numeric summaries) is stored in
extracted_metadata so the artifact detail view can show a rich overview of
the spreadsheet without loading all chunks.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any

import pandas as pd

from ingestion.base import BaseParser, ParseResult


def _clean_val(val: Any) -> str:
    """
    Sanitize a single cell value from a pandas DataFrame.

    Args:
        val: Raw cell value; may be NaN, None, int, float, or str.

    Returns:
        Empty string for NaN/None, otherwise the value cast to str and stripped.
    """
    if pd.isna(val):
        return ""
    return str(val).strip()


class XlsxParser(BaseParser):
    """
    Parses XLSX files into a list of structured row dicts via pandas.

    Each sheet is iterated row-by-row.  Every data row is serialised as
    ``"Col: Val | Col: Val | …"`` — a self-describing format that embeds
    well and is readable in plain-text search results.  Fully empty rows
    (all NaN after reading) are discarded.

    The extracted_metadata dict carries sheet-level statistics:
      sheet_names        — ordered list of sheet names
      sheet_row_counts   — {sheet_name: int}  data rows per sheet
      sheet_column_counts — {sheet_name: int} columns per sheet
      total_rows         — sum of all data rows across all sheets
    """

    def parse(self, path: Path, file_hash: str, size_bytes: int) -> ParseResult:
        """
        Parse an XLSX file and return a normalised ParseResult.

        Args:
            path:       Absolute path to the .xlsx file on disk.
            file_hash:  SHA-256 hex digest (pre-computed, passed through).
            size_bytes: Raw file size in bytes (passed through).

        Returns:
            ParseResult with:
              - xlsx_rows: list of dicts, one per data row:
                  {"sheet": str, "row_index": int, "text": str, "headers": list[str]}
              - extracted_metadata: sheet stats (names, row/column counts)
              - markdown_content: "" (unused for XLSX)
        """
        # Open the Excel file once; reuse for all sheets
        xl = pd.ExcelFile(str(path))
        sheet_names = xl.sheet_names

        all_rows: list[dict] = []
        sheet_row_counts: dict[str, int] = {}
        sheet_column_counts: dict[str, int] = {}

        for sheet_name in sheet_names:
            # dtype=str prevents pandas from coercing numerics to float (e.g.
            # "2024" staying "2024" rather than becoming 2024.0)
            df = xl.parse(sheet_name, dtype=str)
            df = df.dropna(how="all")   # discard completely blank rows
            df = df.fillna("")          # replace remaining NaN with empty str

            headers = list(df.columns)
            sheet_row_counts[sheet_name] = len(df)
            sheet_column_counts[sheet_name] = len(headers)

            for row_index, (_, row) in enumerate(df.iterrows()):
                # Build "Col: Val | Col: Val" string — skip cells with empty values
                # so the chunk is dense with actual content, not "Col: | Col: |…"
                parts = [
                    f"{col}: {_clean_val(val)}"
                    for col, val in row.items()
                    if _clean_val(val)           # skip empty values
                ]
                if not parts:
                    continue  # entire row was empty — skip it

                row_text = " | ".join(parts)
                all_rows.append({
                    "sheet": sheet_name,
                    "row_index": row_index,     # 0-based position in the sheet's data rows
                    "text": row_text,
                    "headers": headers,         # column names, used by XlsxChunker for context
                })

        return ParseResult(
            filename=path.name,
            file_type="xlsx",
            size_bytes=size_bytes,
            file_hash=file_hash,
            extracted_metadata={
                "sheet_names": sheet_names,
                "sheet_row_counts": sheet_row_counts,
                "sheet_column_counts": sheet_column_counts,
                "total_rows": sum(sheet_row_counts.values()),
            },
            xlsx_rows=all_rows,
        )
