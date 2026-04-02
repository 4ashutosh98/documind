"""
SQLAlchemy ORM model for the ``chunks`` table.

A chunk is a retrieval-sized slice of an artifact's text content with full
provenance back to its source location (page, section, row range, etc.).

The chunk text is also indexed in the ``chunks_fts`` FTS5 virtual table via
three sync triggers defined in database.py, enabling BM25 keyword search.

Provenance schema per file type
--------------------------------
PDF / DOCX (MarkdownChunker):
    {"section": "Introduction", "breadcrumb": "Chapter 1 > Section 2",
     "char_start": 1234, "char_end": 1890}

XLSX (XlsxChunker):
    {"sheet": "Revenue", "row_start": 10, "row_end": 19,
     "section": "Columns: Date | Amount | Category"}

Indices:
    ix_chunks_artifact_id      — fast "all chunks for artifact" lookup
    ix_chunks_artifact_index   — ordered retrieval by (artifact_id, chunk_index)
"""
from sqlalchemy import Column, String, Integer, ForeignKey, Text, Index
from sqlalchemy.orm import relationship
from database import Base


class Chunk(Base):
    """
    One retrieval unit from an artifact.

    Columns
    -------
    id : str (UUID)
        Primary key.
    artifact_id : str (FK → artifacts.id)
        Parent artifact.  ON DELETE CASCADE removes all chunks when the artifact
        is deleted.
    chunk_index : int
        Zero-based position within the artifact's chunk sequence.  Used for
        ordered display in the artifact detail modal.
    text : str
        The chunk content as stored in SQLite.  For PDF/DOCX this is the
        markdown slice (possibly with [Metadata]/[Context]/[Content] prefixes
        if contextual enrichment ran).  For XLSX it is the "Col: Val | …"
        row window with a Columns header.
    provenance : str (JSON)
        Source location within the original document.  Schema varies by file
        type — see module docstring for details.
    chunk_type : str
        ``"text"`` | ``"heading"`` | ``"table_row"``
        Used by the UI to apply different badge colours and icons.
    token_count : int | None
        Whitespace-split token count (~10 % of true BPE tokens for English).
        None if not computed.

    Relationships
    -------------
    artifact : Artifact
        The parent artifact.  back_populates mirrors ``Artifact.chunks``.
    """

    __tablename__ = "chunks"

    id = Column(String, primary_key=True)
    artifact_id = Column(
        String,
        ForeignKey("artifacts.id", ondelete="CASCADE"),  # delete chunks when artifact deleted
        nullable=False,
    )
    chunk_index = Column(Integer, nullable=False)  # 0-based position in artifact
    text = Column(Text, nullable=False)

    # Provenance as JSON string — contents vary by file type:
    # PDF/DOCX: {"section": str|null, "breadcrumb": str|null, "char_start": int, "char_end": int}
    # XLSX:     {"sheet": str, "row_start": int, "row_end": int, "section": str|null}
    provenance = Column(String, nullable=False, default="{}")

    chunk_type = Column(String, nullable=False, default="text")  # "text"|"table_row"|"heading"
    token_count = Column(Integer, nullable=True)

    artifact = relationship("Artifact", back_populates="chunks")

    __table_args__ = (
        # Covering index for "SELECT * FROM chunks WHERE artifact_id = ?"
        Index("ix_chunks_artifact_id", "artifact_id"),
        # Ordered retrieval: all chunks for an artifact sorted by position
        Index("ix_chunks_artifact_index", "artifact_id", "chunk_index"),
    )
