"""
SQLAlchemy ORM model for the ``artifacts`` table.

An artifact is one user's logical document — the result of uploading a file.
Multiple users may reference the same physical blob (same SHA-256 hash) via
different artifact records, enabling blob deduplication without data loss.

Key design decisions
---------------------
extracted_metadata (JSON blob):
    Filetype-specific intrinsic metadata lives here rather than in sparse
    nullable columns.  PDF gets page_count, DOCX gets section_count, XLSX
    gets sheet_names — one flexible column beats many nullable ones.

embedding_status:
    Tracks the lifecycle of vector indexing so the frontend can show an
    accurate status icon for each file:
      "none"    → embeddings disabled or not yet started
      "pending" → background task is running
      "ready"   → vectors are in ChromaDB; hybrid search is available

parent_id (self-referential FK):
    Forms a version chain per (user_id, filename).  When a file is re-uploaded
    with a different hash, a new artifact is created with parent_id pointing
    at the previous version.  Deletion orphans children (SET NULL) rather than
    cascade-deleting the whole chain.

Indices:
    ix_artifacts_user_hash     — fast exact-duplicate check  (user_id + file_hash)
    ix_artifacts_user_filename — fast version-chain lookup   (user_id + filename)
    ix_artifacts_parent        — fast child traversal        (parent_id)
    ix_artifacts_file_hash     — fast cross-user metadata reuse check
"""
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from database import Base


class Artifact(Base):
    """
    Represents one user's document, tracking its full lifecycle from upload to indexing.

    Columns
    -------
    id : str (UUID)
        Primary key.
    user_id : str
        Owner of this artifact.  All queries are scoped by user_id so users
        never see each other's data.
    filename : str
        Original filename as uploaded (e.g. ``report.pdf``).
    file_type : str
        Lowercase extension without the dot: ``"pdf"``, ``"docx"``, or ``"xlsx"``.
    size_bytes : int
        Raw file size in bytes.
    file_hash : str
        SHA-256 hex digest of the file content.  Used for dedup and for
        locating the blob at ``uploads/<hash>.<ext>``.
    extracted_metadata : str (JSON)
        Filetype-specific intrinsic metadata:
          PDF  → {"title", "author", "page_count", "headings": [...]}
          DOCX → {"title", "author", "section_count", "headings": [...]}
          XLSX → {"sheet_names": [...], "sheet_row_counts": {...}}
        Also stores ``indexed_features`` dict after embedding completes.
    uploaded_by : str
        Human-readable label for the uploader, same as user_id in this mock system.
    version_number : int
        Starts at 1.  Incremented each time the same filename is re-uploaded
        with a different hash.
    parent_id : str | None
        UUID of the previous version artifact, or None for the first version.
        On DELETE SET NULL so deleting an ancestor does not cascade.
    embedding_status : str
        ``"none"`` | ``"pending"`` | ``"ready"``
    first_seen : datetime
        Set once at artifact creation — never updated.
    last_seen : datetime
        Updated each time an exact duplicate is uploaded, tracking activity.
    upload_timestamp : datetime
        Same as first_seen; surfaced separately for display purposes.

    Relationships
    -------------
    chunks : list[Chunk]
        All chunks belonging to this artifact.  Cascade delete removes chunks
        when the artifact is deleted.
    """

    __tablename__ = "artifacts"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False)          # "user1" | "user2" | "user3"
    filename = Column(String, nullable=False)
    file_type = Column(String, nullable=False)        # "pdf" | "docx" | "xlsx"
    size_bytes = Column(Integer, nullable=False)
    file_hash = Column(String, nullable=False)        # SHA-256 hex digest

    # Filetype-specific intrinsic metadata as JSON string.
    # Avoids sparse nullable columns since fields differ across file types.
    # PDF:  {"title", "author", "page_count", "headings": [...]}
    # DOCX: {"title", "author", "section_count", "headings": [...]}
    # XLSX: {"sheet_names": [...], "sheet_row_counts": {...}}
    # Post-indexing: also contains {"indexed_features": {"doc2query": bool, ...}}
    extracted_metadata = Column(String, nullable=False, default="{}")

    uploaded_by = Column(String, nullable=False)      # human-readable label, same as user_id
    version_number = Column(Integer, nullable=False, default=1)
    parent_id = Column(
        String,
        ForeignKey("artifacts.id", ondelete="SET NULL"),  # orphan children on parent delete
        nullable=True,
    )

    # Embedding lifecycle:
    # "none"    → not yet embedded (embeddings disabled or upload just finished)
    # "pending" → background task is running (yellow triangle in UI)
    # "ready"   → vectors in ChromaDB, hybrid search available (green checkmark in UI)
    embedding_status = Column(String, nullable=False, default="none")

    first_seen = Column(DateTime, nullable=False)   # set once at creation
    last_seen = Column(DateTime, nullable=False)    # updated on duplicate upload
    upload_timestamp = Column(DateTime, nullable=False)

    # Cascade all-delete-orphan: when an artifact is deleted, SQLAlchemy
    # also deletes all its chunks in the same transaction.
    chunks = relationship("Chunk", back_populates="artifact", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_artifacts_user_hash", "user_id", "file_hash"),    # dedup check
        Index("ix_artifacts_user_filename", "user_id", "filename"),  # version chain lookup
        Index("ix_artifacts_parent", "parent_id"),                   # version traversal
        Index("ix_artifacts_file_hash", "file_hash"),                # cross-user metadata reuse
    )
