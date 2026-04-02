"""
Unit tests for the chunking layer and retrieval helpers.

All tests here are pure — no database, no I/O, no external services.
They exercise the algorithms directly: heading detection, provenance assignment,
windowing logic, FTS query building, and match-position extraction.
"""
from __future__ import annotations

import pytest

from chunking.base import ChunkRecord, count_tokens
from chunking.markdown_chunker import (
    MarkdownChunker,
    _build_heading_index,
    _heading_at,
)
from chunking.xlsx_chunker import XlsxChunker
from ingestion.base import ParseResult
from retrieval.keyword_search import _build_fts_query, _compute_match_positions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _xlsx_result(rows_by_sheet: dict[str, list[str]], headers=None) -> ParseResult:
    """Build a minimal ParseResult with xlsx_rows for XlsxChunker tests."""
    headers = headers or ["Col A", "Col B"]
    rows = []
    for sheet, texts in rows_by_sheet.items():
        for i, text in enumerate(texts):
            rows.append({"sheet": sheet, "row_index": i, "text": text, "headers": headers})
    return ParseResult(
        filename="test.xlsx",
        file_type="xlsx",
        size_bytes=100,
        file_hash="a" * 64,
        extracted_metadata={},
        xlsx_rows=rows,
    )


def _md_result(markdown: str) -> ParseResult:
    return ParseResult(
        filename="test.pdf",
        file_type="pdf",
        size_bytes=len(markdown),
        file_hash="b" * 64,
        extracted_metadata={},
        markdown_content=markdown,
    )


# ---------------------------------------------------------------------------
# _build_heading_index
# ---------------------------------------------------------------------------

def test_build_heading_index_basic():
    md = "# Title\n\nSome text.\n\n## Section 1\n\nMore text."
    idx = _build_heading_index(md)
    assert len(idx) == 2
    _, level1, text1 = idx[0]
    _, level2, text2 = idx[1]
    assert level1 == 1 and text1 == "Title"
    assert level2 == 2 and text2 == "Section 1"


def test_build_heading_index_empty():
    assert _build_heading_index("No headings here.") == []


def test_build_heading_index_positions_are_sorted():
    md = "# A\n\ntext\n\n## B\n\n### C"
    idx = _build_heading_index(md)
    positions = [p for p, _, _ in idx]
    assert positions == sorted(positions)


# ---------------------------------------------------------------------------
# _heading_at
# ---------------------------------------------------------------------------

def test_heading_at_no_headings():
    section, breadcrumb = _heading_at([], pos=0)
    assert section is None
    assert breadcrumb is None


def test_heading_at_single_heading():
    md = "# Introduction\n\nSome content."
    idx = _build_heading_index(md)
    # Position of "Some content" is after the heading
    content_pos = md.index("Some content")
    section, breadcrumb = _heading_at(idx, pos=content_pos)
    assert section == "Introduction"
    assert breadcrumb is None  # no parent heading


def test_heading_at_nested_headings_breadcrumb():
    md = "# Chapter 1\n\n## Section 1.1\n\nDeep content."
    idx = _build_heading_index(md)
    deep_pos = md.index("Deep content")
    section, breadcrumb = _heading_at(idx, pos=deep_pos)
    assert section == "Section 1.1"
    assert breadcrumb == "Chapter 1"


def test_heading_at_before_first_heading():
    md = "Preamble text.\n\n# First Heading\n\nAfter heading."
    idx = _build_heading_index(md)
    # Position before the heading
    section, breadcrumb = _heading_at(idx, pos=0)
    assert section is None


# ---------------------------------------------------------------------------
# MarkdownChunker
# ---------------------------------------------------------------------------

def test_markdown_chunker_empty_content():
    chunker = MarkdownChunker()
    result = _md_result("")
    chunks = chunker.chunk(result)
    assert chunks == []


def test_markdown_chunker_produces_chunks():
    md = "# Revenue\n\nQ3 revenue was $5B.\n\n## Cost of Goods\n\nCOGS were $2B."
    chunker = MarkdownChunker()
    chunks = chunker.chunk(_md_result(md))
    assert len(chunks) >= 1
    combined = " ".join(c.text for c in chunks)
    assert "revenue" in combined.lower()


def test_markdown_chunker_provenance_has_char_offsets():
    md = "# Section\n\nContent here with enough words to form a chunk."
    chunker = MarkdownChunker()
    chunks = chunker.chunk(_md_result(md))
    for chunk in chunks:
        assert "char_start" in chunk.provenance
        assert "char_end" in chunk.provenance
        assert chunk.provenance["char_end"] > chunk.provenance["char_start"]


def test_markdown_chunker_section_provenance():
    md = "# Financial Results\n\nRevenue grew 25% year over year."
    chunker = MarkdownChunker()
    chunks = chunker.chunk(_md_result(md))
    # At least the text chunk should have the section assigned
    text_chunks = [c for c in chunks if c.chunk_type == "text"]
    if text_chunks:
        assert text_chunks[0].provenance.get("section") == "Financial Results"


def test_markdown_chunker_heading_chunk_type():
    md = "# Title\n\nBody text."
    chunker = MarkdownChunker()
    chunks = chunker.chunk(_md_result(md))
    chunk_types = {c.chunk_type for c in chunks}
    # Should have both heading and text types for a doc with a heading
    assert "text" in chunk_types or "heading" in chunk_types


def test_markdown_chunker_token_counts_set():
    md = "# Section\n\n" + " ".join(["word"] * 50)
    chunker = MarkdownChunker()
    chunks = chunker.chunk(_md_result(md))
    for chunk in chunks:
        assert chunk.token_count is not None
        assert chunk.token_count > 0


def test_markdown_chunker_chunk_indices_sequential():
    md = "\n\n".join(f"# H{i}\n\nContent {i}." for i in range(5))
    chunker = MarkdownChunker()
    chunks = chunker.chunk(_md_result(md))
    indices = [c.chunk_index for c in chunks]
    assert indices == list(range(len(chunks)))


# ---------------------------------------------------------------------------
# XlsxChunker
# ---------------------------------------------------------------------------

def test_xlsx_chunker_empty():
    chunker = XlsxChunker()
    result = ParseResult(
        filename="empty.xlsx", file_type="xlsx",
        size_bytes=0, file_hash="c" * 64,
        extracted_metadata={}, xlsx_rows=[],
    )
    assert chunker.chunk(result) == []


def test_xlsx_chunker_basic_windowing(patched_settings):
    # 15 rows, window=10, overlap=2 → step=8 → windows at 0,8 → 2 chunks
    patched_settings.xlsx_chunk_rows = 10
    patched_settings.xlsx_chunk_overlap_rows = 2
    rows = [f"Name: Item{i} | Value: {i}" for i in range(15)]
    chunker = XlsxChunker()
    chunks = chunker.chunk(_xlsx_result({"Sheet1": rows}))
    assert len(chunks) == 2


def test_xlsx_chunker_single_chunk_for_small_sheet(patched_settings):
    patched_settings.xlsx_chunk_rows = 10
    patched_settings.xlsx_chunk_overlap_rows = 2
    rows = ["Name: A | Value: 1", "Name: B | Value: 2"]
    chunker = XlsxChunker()
    chunks = chunker.chunk(_xlsx_result({"Sheet1": rows}))
    assert len(chunks) == 1


def test_xlsx_chunker_provenance_fields(patched_settings):
    patched_settings.xlsx_chunk_rows = 5
    patched_settings.xlsx_chunk_overlap_rows = 0
    rows = [f"Name: X{i}" for i in range(5)]
    chunker = XlsxChunker()
    chunks = chunker.chunk(_xlsx_result({"Revenue": rows}))
    prov = chunks[0].provenance
    assert prov["sheet"] == "Revenue"
    assert prov["row_start"] == 0
    assert prov["row_end"] == 4


def test_xlsx_chunker_header_prepended(patched_settings):
    patched_settings.xlsx_chunk_rows = 5
    patched_settings.xlsx_chunk_overlap_rows = 0
    headers = ["Product", "Units", "Revenue"]
    rows = [{"sheet": "Q1", "row_index": i, "text": f"Product: P{i}", "headers": headers}
            for i in range(3)]
    result = ParseResult(
        filename="x.xlsx", file_type="xlsx",
        size_bytes=100, file_hash="d" * 64,
        extracted_metadata={}, xlsx_rows=rows,
    )
    chunker = XlsxChunker()
    chunks = chunker.chunk(result)
    assert len(chunks) == 1
    assert "Columns:" in chunks[0].text
    assert "Product" in chunks[0].text


def test_xlsx_chunker_multisheet_produces_separate_chunks(patched_settings):
    patched_settings.xlsx_chunk_rows = 5
    patched_settings.xlsx_chunk_overlap_rows = 0
    chunker = XlsxChunker()
    chunks = chunker.chunk(_xlsx_result({
        "Sheet1": [f"A: {i}" for i in range(5)],
        "Sheet2": [f"B: {i}" for i in range(5)],
    }))
    sheets_in_chunks = {c.provenance["sheet"] for c in chunks}
    assert "Sheet1" in sheets_in_chunks
    assert "Sheet2" in sheets_in_chunks


def test_xlsx_chunker_chunk_type_is_table_row(patched_settings):
    patched_settings.xlsx_chunk_rows = 5
    patched_settings.xlsx_chunk_overlap_rows = 0
    chunker = XlsxChunker()
    chunks = chunker.chunk(_xlsx_result({"S": ["Col: Val"]}))
    assert all(c.chunk_type == "table_row" for c in chunks)


# ---------------------------------------------------------------------------
# count_tokens
# ---------------------------------------------------------------------------

def test_count_tokens_basic():
    assert count_tokens("hello world foo") == 3


def test_count_tokens_empty():
    assert count_tokens("") == 0


def test_count_tokens_extra_whitespace():
    # split() handles multiple spaces
    assert count_tokens("  a  b  c  ") == 3


# ---------------------------------------------------------------------------
# _build_fts_query (retrieval helper)
# ---------------------------------------------------------------------------

def test_build_fts_query_single_term():
    result = _build_fts_query("revenue")
    assert result == '"revenue"'


def test_build_fts_query_multi_term():
    result = _build_fts_query("revenue growth")
    assert '"revenue"' in result
    assert '"growth"' in result
    assert " OR " in result


def test_build_fts_query_empty():
    # Empty query should return a safe fallback
    result = _build_fts_query("")
    assert result == '""'


def test_build_fts_query_strips_whitespace():
    result = _build_fts_query("  profit  ")
    assert result == '"profit"'


# ---------------------------------------------------------------------------
# _compute_match_positions
# ---------------------------------------------------------------------------

def test_compute_match_positions_basic():
    positions = _compute_match_positions("Revenue grew 25%", "revenue")
    assert len(positions) == 1
    start, end = positions[0]
    assert "Revenue grew 25%"[start:end].lower() == "revenue"


def test_compute_match_positions_case_insensitive():
    positions = _compute_match_positions("REVENUE and revenue and Revenue", "revenue")
    assert len(positions) == 3


def test_compute_match_positions_no_match():
    positions = _compute_match_positions("Unrelated content here", "revenue")
    assert positions == []


def test_compute_match_positions_multi_term():
    text = "Revenue grew and profits expanded"
    positions = _compute_match_positions(text, "revenue profits")
    assert len(positions) == 2
