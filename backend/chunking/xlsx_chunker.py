"""
XLSX chunker: row-window chunking per sheet.

Strategy
--------
Groups XLSX_CHUNK_ROWS rows into a single chunk with XLSX_CHUNK_OVERLAP_ROWS
row overlap between consecutive windows.  Each chunk's provenance carries the
sheet name and the inclusive row range (row_start, row_end) so results can be
surfaced as "Sheet: Revenue | Rows 10–19".

Each chunk is prefixed with a "Columns: Col1 | Col2 | …" header line so the
chunk is self-describing — a retrieval system can understand the chunk without
the surrounding context of the full spreadsheet.

Window / overlap parameters
----------------------------
window  = settings.xlsx_chunk_rows          (default 10 rows per chunk)
overlap = settings.xlsx_chunk_overlap_rows  (default 2 rows overlap)
step    = window - overlap                  (advance by step rows per window)

Example with window=10, overlap=2, step=8:
  Window 0: rows  0–9
  Window 1: rows  8–17  (rows 8-9 overlap with Window 0)
  Window 2: rows 16–25  (rows 16-17 overlap with Window 1)
"""
from __future__ import annotations
from collections import defaultdict

from chunking.base import BaseChunker, ChunkRecord, count_tokens
from config import settings
from ingestion.base import ParseResult


class XlsxChunker(BaseChunker):
    """
    Groups spreadsheet rows into overlapping windows, one chunk per window per sheet.

    Processes each sheet independently.  The global chunk_index is incremented
    across all sheets so chunk ordering is consistent for the whole artifact.
    """

    def chunk(self, result: ParseResult) -> list[ChunkRecord]:
        """
        Produce row-window chunks from a ParseResult's xlsx_rows list.

        Args:
            result: ParseResult with a non-empty xlsx_rows list.
                    (XLSX files produce xlsx_rows; PDF/DOCX do not.)

        Returns:
            Ordered list of ChunkRecord objects with chunk_type="table_row".
            Empty if result.xlsx_rows is empty.

        Each ChunkRecord's provenance contains:
            sheet      — sheet name (str)
            row_start  — 0-based index of the first data row in this window
            row_end    — 0-based index of the last data row in this window
            section    — "Columns: Col1 | Col2 | …" header string (or None)
        """
        if not result.xlsx_rows:
            return []

        window = settings.xlsx_chunk_rows           # rows per chunk (default 10)
        overlap = settings.xlsx_chunk_overlap_rows  # overlap between windows (default 2)
        step = max(1, window - overlap)             # rows to advance each iteration

        # Group rows by sheet, preserving document order
        by_sheet: dict[str, list[dict]] = defaultdict(list)
        for row in result.xlsx_rows:
            by_sheet[row["sheet"]].append(row)

        chunks: list[ChunkRecord] = []
        idx = 0  # global chunk index across all sheets

        for sheet_name, rows in by_sheet.items():
            # Build the header context line once per sheet — prepended to every
            # chunk in this sheet so each chunk is self-describing
            headers = rows[0]["headers"] if rows else []
            header_line = "Columns: " + " | ".join(str(h) for h in headers) if headers else ""

            i = 0
            while i < len(rows):
                # Slice window_rows out of the sheet's rows
                window_rows = rows[i : i + window]

                # Join all row texts with newlines to form the chunk content
                row_texts = [r["text"] for r in window_rows]
                content = "\n".join(row_texts)

                # Prepend the header context so the chunk is self-describing
                # even when retrieved without surrounding rows
                full_text = f"{header_line}\n{content}".strip() if header_line else content

                first_row = window_rows[0]["row_index"]
                last_row = window_rows[-1]["row_index"]

                chunks.append(ChunkRecord(
                    text=full_text,
                    chunk_index=idx,
                    chunk_type="table_row",
                    provenance={
                        "sheet": sheet_name,
                        "row_start": first_row,
                        "row_end": last_row,
                        "section": header_line or None,  # used for display in source badges
                    },
                    token_count=count_tokens(full_text),
                ))
                idx += 1
                i += step   # advance by step (window - overlap) rows

        return chunks
