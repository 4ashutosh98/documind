"""
LangChain-based Markdown chunker for PDF and DOCX (Docling output).

Strategy
--------
LangChain's RecursiveCharacterTextSplitter with markdown separators splits
the document by trying each separator in order until chunks are small enough:
  1. H1 heading lines  (``\\n# ``)
  2. H2 → H6 heading lines
  3. Double newlines (paragraph breaks)
  4. Single newlines
  5. Spaces
  6. Characters (last resort)

``add_start_index=True`` makes LangChain store the character offset of each
chunk's start position in the original markdown string.  This is used to
reconstruct section + breadcrumb provenance for every chunk.

Provenance reconstruction
--------------------------
A pre-scan with ``_build_heading_index`` extracts all heading positions once.
For each chunk, ``_heading_at`` walks the heading index maintaining an
ancestor stack to find:
  - ``section``   — the most recent heading text (nearest parent/sibling)
  - ``breadcrumb`` — the chain of ancestor headings joined by " > "
    (e.g. ``"Chapter 2 > Section 3"`` for the current section heading)

Provenance stored per chunk:
  {"section": "<nearest preceding heading>",
   "breadcrumb": "<parent > chain>",
   "char_start": int,
   "char_end": int}
"""
from __future__ import annotations

import re

from langchain_text_splitters import RecursiveCharacterTextSplitter

from chunking.base import BaseChunker, ChunkRecord, count_tokens
from config import settings
from ingestion.base import ParseResult

# Markdown heading separators — tried left-to-right; first match wins.
# Keeping separators ensures heading lines are preserved at chunk boundaries
# rather than being swallowed into surrounding content.
_SEPARATORS = [
    "\n# ", "\n## ", "\n### ", "\n#### ", "\n##### ", "\n###### ",
    "\n\n",  # paragraph break
    "\n",    # line break
    " ",     # word break
    "",      # character break (last resort)
]

# Regex that matches any markdown heading line (1–6 # symbols)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def _build_heading_index(markdown: str) -> list[tuple[int, int, str]]:
    """
    Scan markdown for all heading lines and return their positions.

    Args:
        markdown: Full document markdown string.

    Returns:
        List of (char_pos, level, heading_text) tuples sorted by char_pos
        (ascending), where:
          char_pos     — character offset of the heading in the markdown
          level        — heading depth (1 = H1, 2 = H2, …)
          heading_text — heading text without the leading # characters
    """
    return [
        (m.start(), len(m.group(1)), m.group(2).strip())
        for m in _HEADING_RE.finditer(markdown)
    ]


def _heading_at(
    heading_index: list[tuple[int, int, str]],
    pos: int,
) -> tuple[str | None, str | None]:
    """
    Find the section and breadcrumb provenance for a chunk at character offset ``pos``.

    Walks the heading index maintaining an ancestor stack.  A heading is
    "active" for any position that comes after it and before the next heading
    of the same or higher level.

    Example:
        # Chapter 1         (pos 0)
        ## Section 1.1      (pos 100)
        ### Subsection 1.1.1 (pos 200)

        For a chunk at pos=250:
          section    = "Subsection 1.1.1"
          breadcrumb = "Chapter 1 > Section 1.1"

    Args:
        heading_index: Output of ``_build_heading_index``.
        pos:           Character offset of the chunk's first character.

    Returns:
        (section, breadcrumb) where:
          section    — most recent (innermost) heading text, or None
          breadcrumb — " > "-joined ancestor headings (excluding section),
                       or None if there are no ancestors
    """
    stack: list[tuple[int, str]] = []  # (level, heading_text)

    for h_pos, level, heading_text in heading_index:
        if h_pos > pos:
            # All remaining headings come after this chunk — stop
            break
        # Pop any headings at the same or deeper level than this one;
        # they are no longer active ancestors once we encounter a sibling/parent
        stack = [(l, h) for l, h in stack if l < level]
        stack.append((level, heading_text))

    if not stack:
        return None, None   # chunk precedes any heading

    section = stack[-1][1]  # innermost (most recent) heading
    # Breadcrumb = all ancestors except the section itself, joined with " > "
    breadcrumb = " > ".join(h for _, h in stack[:-1]) if len(stack) > 1 else None
    return section, breadcrumb


class MarkdownChunker(BaseChunker):
    """
    Splits Docling-produced markdown into retrieval-sized chunks with provenance.

    Uses LangChain's RecursiveCharacterTextSplitter configured for markdown
    heading boundaries.  Chunk size and overlap are read from settings
    (chunk_max_tokens and chunk_overlap_tokens, default 300/50 tokens).
    """

    def __init__(self) -> None:
        """
        Initialise the LangChain splitter with markdown separators.

        Configuration (from settings):
          chunk_size    = chunk_max_tokens    (default 300 whitespace-tokens)
          chunk_overlap = chunk_overlap_tokens (default 50 whitespace-tokens)
          length_function = count_tokens      (whitespace-based, zero-dependency)
          keep_separator  = True              (preserve heading lines at boundaries)
          add_start_index = True              (record char offset for provenance)
        """
        self._splitter = RecursiveCharacterTextSplitter(
            separators=_SEPARATORS,
            chunk_size=settings.chunk_max_tokens,
            chunk_overlap=settings.chunk_overlap_tokens,
            length_function=count_tokens,   # whitespace-based token count
            keep_separator=True,            # don't discard the heading text
            add_start_index=True,           # needed for char_start provenance
        )

    def chunk(self, result: ParseResult) -> list[ChunkRecord]:
        """
        Split a ParseResult's markdown content into ChunkRecord objects.

        Args:
            result: ParseResult with a non-empty markdown_content string.
                    (PDF and DOCX produce markdown; XLSX does not.)

        Returns:
            Ordered list of ChunkRecord objects.  Empty if markdown_content
            is empty or contains only whitespace.

        Each ChunkRecord's provenance contains:
            section    — nearest preceding heading text (or None)
            breadcrumb — ancestor heading chain (or None)
            char_start — character offset in the original markdown
            char_end   — char_start + len(chunk_text)
        """
        if not result.markdown_content:
            return []

        text = result.markdown_content

        # Pre-compute heading positions once for provenance assignment
        heading_index = _build_heading_index(text)

        # heading → page_no map from docling_parser (PDF/DOCX only).
        # Keys are heading texts stripped of whitespace; values are 1-based page numbers.
        # Missing for XLSX and for PDFs parsed before this feature was added.
        heading_page_map: dict[str, int] = {}
        if result.extracted_metadata:
            raw_map = result.extracted_metadata.get("heading_page_map")
            if isinstance(raw_map, dict):
                heading_page_map = raw_map

        # LangChain returns Document objects; page_content is the chunk text
        docs = self._splitter.create_documents([text])

        chunks: list[ChunkRecord] = []
        for idx, doc in enumerate(docs):
            chunk_text = doc.page_content
            if not chunk_text.strip():
                continue    # skip whitespace-only splits

            # Retrieve the character offset stored by add_start_index=True
            start = doc.metadata.get("start_index", 0)
            end = start + len(chunk_text)

            section, breadcrumb = _heading_at(heading_index, start)

            # Look up the page number for the nearest heading.
            # Falls back to the breadcrumb's last ancestor if the section itself
            # isn't in the map (e.g. Docling used a slightly different label).
            page: int | None = None
            if section and heading_page_map:
                page = heading_page_map.get(section)
                if page is None and breadcrumb:
                    # Try each ancestor in reverse order (innermost first)
                    for ancestor in reversed(breadcrumb.split(" > ")):
                        page = heading_page_map.get(ancestor.strip())
                        if page is not None:
                            break

            # Classify chunk type: heading chunks start with # characters
            chunk_type = "heading" if chunk_text.lstrip().startswith("#") else "text"

            chunks.append(ChunkRecord(
                text=chunk_text,
                chunk_index=idx,
                chunk_type=chunk_type,
                provenance={
                    "page": page,
                    "section": section,
                    "breadcrumb": breadcrumb,
                    "char_start": start,
                    "char_end": end,
                },
                token_count=count_tokens(chunk_text),
            ))

        return chunks
