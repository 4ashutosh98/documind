"""
Artifact storage: deduplication, versioning, and deletion with blob GC.

Dedup logic (per-user, per-filename version chains)
-----------------------------------------------------
1. Same user + same hash        → duplicate. Bump last_seen. No new record.
2. Same user + same filename    → new version. Increment version_number, set parent_id.
3. Otherwise                    → fresh artifact with version_number=1.

Blob reuse:
    If any artifact (any user) already has this file_hash, the file was already
    parsed.  The upload pipeline copies extracted_metadata so we skip re-parsing.

Deletion GC
-----------
1. Orphan any child versions (SET parent_id = NULL).
2. Reference-count the blob: count other artifacts with the same hash.
3. DELETE the artifact record (cascades to chunks via FK).
4. Delete the physical blob from disk only if reference count is now 0.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from sqlalchemy.orm import Session

from config import settings
from models.artifact import Artifact
from ingestion.base import ParseResult


# ---------------------------------------------------------------------------
# Internal result type for the upload pipeline
# ---------------------------------------------------------------------------

class UpsertResult:
    """
    Carries the outcome of an artifact upsert operation.

    Used internally by the upload pipeline to decide whether to parse the file
    fresh or reuse existing data.

    Attributes
    ----------
    artifact_id : str
        UUID of the artifact record (new or existing).
    status : str
        ``"created"`` | ``"duplicate"`` | ``"new_version"``
    version_number : int
        Current version number of the artifact.
    extracted_metadata : dict
        Pre-parsed metadata (from the reuse path) or empty dict (fresh parse).
    needs_parsing : bool
        False when metadata was reused from an existing artifact (same hash);
        True when fresh Docling/pandas parsing is required.
    """

    def __init__(
        self,
        artifact_id: str,
        status: Literal["created", "duplicate", "new_version"],
        version_number: int,
        extracted_metadata: dict,
        needs_parsing: bool,
    ):
        self.artifact_id = artifact_id
        self.status = status
        self.version_number = version_number
        self.extracted_metadata = extracted_metadata
        self.needs_parsing = needs_parsing  # False = reuse existing metadata + chunks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_duplicate(db: Session, user_id: str, file_hash: str) -> Artifact | None:
    """
    Check if this user has already uploaded the exact same file (same SHA-256).

    Args:
        db:        SQLAlchemy session.
        user_id:   The user performing the upload.
        file_hash: SHA-256 hex digest of the uploaded bytes.

    Returns:
        The existing Artifact ORM object if found, otherwise None.
    """
    return (
        db.query(Artifact)
        .filter(Artifact.user_id == user_id, Artifact.file_hash == file_hash)
        .first()
    )


def get_latest_version_by_filename(db: Session, user_id: str, filename: str) -> Artifact | None:
    """
    Return the highest-version artifact for this user+filename combination.

    Used to detect when a re-upload with a *different* hash should start a new
    version rather than a fresh artifact.  Returns None if the user has never
    uploaded a file with this filename before.

    Args:
        db:       SQLAlchemy session.
        user_id:  The uploading user.
        filename: Original filename as uploaded (e.g. ``"report.pdf"``).

    Returns:
        The most recent (highest version_number) Artifact for this combination,
        or None if no prior version exists.
    """
    return (
        db.query(Artifact)
        .filter(Artifact.user_id == user_id, Artifact.filename == filename)
        .order_by(Artifact.version_number.desc())
        .first()
    )


def find_existing_metadata(db: Session, file_hash: str) -> dict | None:
    """
    Check if any artifact (any user) has already parsed this file hash.

    If so, return their extracted_metadata so we can skip re-parsing.
    This is the cross-user metadata reuse fast path — if User 2 uploads the
    same file as User 1, we copy User 1's parsed metadata rather than running
    Docling again.

    Args:
        db:        SQLAlchemy session.
        file_hash: SHA-256 hex digest.

    Returns:
        Parsed extracted_metadata dict if found, or None if this hash is new.
    """
    existing = db.query(Artifact).filter(Artifact.file_hash == file_hash).first()
    if existing:
        try:
            return json.loads(existing.extracted_metadata)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def create_artifact(
    db: Session,
    user_id: str,
    parse_result: ParseResult,
    version_number: int = 1,
    parent_id: str | None = None,
) -> Artifact:
    """
    Create and persist a new Artifact record.

    Args:
        db:             SQLAlchemy session.
        user_id:        Owner of the artifact.
        parse_result:   Normalised parser output (filename, file_type, metadata, etc.).
        version_number: Version number for this artifact (default 1 for new uploads).
        parent_id:      UUID of the previous version, or None for first version.

    Returns:
        The newly created and committed Artifact ORM instance.
    """
    now = datetime.now(timezone.utc)
    artifact = Artifact(
        id=str(uuid.uuid4()),
        user_id=user_id,
        filename=parse_result.filename,
        file_type=parse_result.file_type,
        size_bytes=parse_result.size_bytes,
        file_hash=parse_result.file_hash,
        extracted_metadata=json.dumps(parse_result.extracted_metadata),
        uploaded_by=user_id,
        version_number=version_number,
        parent_id=parent_id,
        embedding_status="none",    # embedding starts in background after this returns
        first_seen=now,
        last_seen=now,
        upload_timestamp=now,
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    return artifact


def touch_last_seen(db: Session, artifact: Artifact) -> Artifact:
    """
    Update last_seen to now for a duplicate upload.

    Called when an exact-duplicate file is re-uploaded — we do not create a
    new artifact but do record when the file was last seen.

    Args:
        db:       SQLAlchemy session.
        artifact: The existing Artifact to update.

    Returns:
        The refreshed Artifact with updated last_seen.
    """
    artifact.last_seen = datetime.now(timezone.utc)
    db.commit()
    db.refresh(artifact)
    return artifact


def delete_artifact(db: Session, artifact: Artifact) -> bool:
    """
    Delete an artifact record with full GC.

    Steps:
    1. Orphan any child versions pointing to this artifact
       (SET parent_id = NULL so version chains are not broken entirely).
    2. Count remaining artifacts referencing the same blob hash.
    3. DELETE the artifact record (cascades to all its chunks via FK).
    4. Remove the physical blob from disk if no other artifact references it.

    Args:
        db:       SQLAlchemy session.
        artifact: The Artifact ORM instance to delete.

    Returns:
        True if the physical blob file was deleted from disk (last reference),
        False if the blob is still needed by other artifact records.
    """
    file_hash = artifact.file_hash

    # 1. Orphan children — prevent their parent_id from pointing to a deleted row
    db.query(Artifact).filter(Artifact.parent_id == artifact.id).update(
        {"parent_id": None}, synchronize_session=False
    )

    # 2. Count remaining references to this blob (excluding the artifact being deleted)
    remaining = (
        db.query(Artifact)
        .filter(Artifact.file_hash == file_hash, Artifact.id != artifact.id)
        .count()
    )

    # 3. Delete artifact record (ON DELETE CASCADE removes all child chunks)
    db.delete(artifact)
    db.commit()

    # 4. Remove blob from disk only if no other artifact references it
    blob_deleted = False
    if remaining == 0:
        ext = "." + artifact.file_type
        blob_path = Path(settings.upload_dir) / f"{file_hash}{ext}"
        if blob_path.exists():
            blob_path.unlink()
            blob_deleted = True

    return blob_deleted


def get_artifact_by_id(db: Session, artifact_id: str) -> Artifact | None:
    """
    Fetch a single artifact by its UUID primary key.

    Args:
        db:          SQLAlchemy session.
        artifact_id: UUID string.

    Returns:
        Artifact ORM instance, or None if not found.
    """
    return db.query(Artifact).filter(Artifact.id == artifact_id).first()


def list_artifacts(db: Session, user_id: str) -> list[Artifact]:
    """
    Return all artifacts for a user, newest first.

    Args:
        db:      SQLAlchemy session.
        user_id: The user whose artifacts to list.

    Returns:
        List of Artifact ORM instances ordered by upload_timestamp descending.
    """
    return (
        db.query(Artifact)
        .filter(Artifact.user_id == user_id)
        .order_by(Artifact.upload_timestamp.desc())
        .all()
    )
