"""
Storage layer tests: artifact_store + chunk_store.

Uses a real in-memory SQLite database (no mocks for DB operations).
Tests cover the full dedup/versioning/deletion state machine and
the blob GC reference-counting logic.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from chunking.base import ChunkRecord
from ingestion.base import ParseResult
from storage import artifact_store, chunk_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_result(
    filename: str = "report.pdf",
    file_type: str = "pdf",
    file_hash: str | None = None,
    metadata: dict | None = None,
) -> ParseResult:
    return ParseResult(
        filename=filename,
        file_type=file_type,
        size_bytes=1024,
        file_hash=file_hash or uuid.uuid4().hex * 2,  # 64 hex chars
        extracted_metadata=metadata or {"page_count": 3},
        markdown_content="# Title\n\nSome content.",
    )


def _make_artifact(db_session, user_id="user1", filename="report.pdf", file_hash=None):
    pr = _parse_result(filename=filename, file_hash=file_hash)
    return artifact_store.create_artifact(db_session, user_id=user_id, parse_result=pr)


def _make_chunks(count: int = 3) -> list[ChunkRecord]:
    return [
        ChunkRecord(
            text=f"Chunk {i} content with some words.",
            chunk_index=i,
            chunk_type="text",
            provenance={"section": "Intro", "char_start": i * 100, "char_end": i * 100 + 50},
            token_count=6,
        )
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# create_artifact
# ---------------------------------------------------------------------------

def test_create_artifact_returns_artifact_with_id(db_session):
    pr = _parse_result()
    artifact = artifact_store.create_artifact(db_session, user_id="user1", parse_result=pr)
    assert artifact.id is not None
    assert artifact.user_id == "user1"
    assert artifact.filename == pr.filename
    assert artifact.version_number == 1
    assert artifact.parent_id is None
    assert artifact.embedding_status == "none"


def test_create_artifact_stores_metadata_as_json(db_session):
    metadata = {"page_count": 5, "title": "Annual Report"}
    pr = _parse_result(metadata=metadata)
    artifact = artifact_store.create_artifact(db_session, user_id="user1", parse_result=pr)
    stored = json.loads(artifact.extracted_metadata)
    assert stored["page_count"] == 5
    assert stored["title"] == "Annual Report"


def test_create_artifact_timestamps_set(db_session):
    artifact = _make_artifact(db_session)
    assert artifact.first_seen is not None
    assert artifact.last_seen is not None
    assert artifact.upload_timestamp is not None


# ---------------------------------------------------------------------------
# check_duplicate
# ---------------------------------------------------------------------------

def test_check_duplicate_same_user_same_hash(db_session):
    artifact = _make_artifact(db_session, user_id="user1")
    found = artifact_store.check_duplicate(db_session, "user1", artifact.file_hash)
    assert found is not None
    assert found.id == artifact.id


def test_check_duplicate_different_user_returns_none(db_session):
    artifact = _make_artifact(db_session, user_id="user1")
    # Different user uploading same file hash → not a duplicate for them
    found = artifact_store.check_duplicate(db_session, "user2", artifact.file_hash)
    assert found is None


def test_check_duplicate_different_hash_returns_none(db_session):
    _make_artifact(db_session, user_id="user1")
    found = artifact_store.check_duplicate(db_session, "user1", "0" * 64)
    assert found is None


# ---------------------------------------------------------------------------
# Version chain
# ---------------------------------------------------------------------------

def test_version_chain_increments_version_number(db_session):
    v1 = _make_artifact(db_session, user_id="user1", filename="report.pdf")
    pr_v2 = _parse_result(filename="report.pdf")  # different hash
    v2 = artifact_store.create_artifact(
        db_session,
        user_id="user1",
        parse_result=pr_v2,
        version_number=2,
        parent_id=v1.id,
    )
    assert v2.version_number == 2
    assert v2.parent_id == v1.id


def test_get_latest_version_by_filename(db_session):
    v1 = _make_artifact(db_session, user_id="user1", filename="report.pdf")
    pr_v2 = _parse_result(filename="report.pdf")
    v2 = artifact_store.create_artifact(
        db_session, user_id="user1", parse_result=pr_v2,
        version_number=2, parent_id=v1.id,
    )
    latest = artifact_store.get_latest_version_by_filename(db_session, "user1", "report.pdf")
    assert latest.id == v2.id


def test_get_latest_version_by_filename_not_found(db_session):
    result = artifact_store.get_latest_version_by_filename(db_session, "user1", "missing.pdf")
    assert result is None


# ---------------------------------------------------------------------------
# find_existing_metadata (cross-user metadata reuse)
# ---------------------------------------------------------------------------

def test_find_existing_metadata_returns_dict(db_session):
    metadata = {"page_count": 7, "author": "Alice"}
    pr = _parse_result(metadata=metadata)
    artifact_store.create_artifact(db_session, user_id="user1", parse_result=pr)

    found = artifact_store.find_existing_metadata(db_session, pr.file_hash)
    assert found is not None
    assert found["page_count"] == 7


def test_find_existing_metadata_returns_none_for_unknown_hash(db_session):
    found = artifact_store.find_existing_metadata(db_session, "0" * 64)
    assert found is None


# ---------------------------------------------------------------------------
# touch_last_seen
# ---------------------------------------------------------------------------

def test_touch_last_seen_updates_timestamp(db_session):
    artifact = _make_artifact(db_session)
    original_ts = artifact.last_seen

    import time
    time.sleep(0.01)  # ensure measurable time difference

    updated = artifact_store.touch_last_seen(db_session, artifact)
    assert updated.last_seen >= original_ts


# ---------------------------------------------------------------------------
# chunk_store
# ---------------------------------------------------------------------------

def test_bulk_insert_creates_chunk_rows(db_session):
    artifact = _make_artifact(db_session)
    records = _make_chunks(3)
    persisted = chunk_store.bulk_insert(db_session, artifact.id, records)
    assert len(persisted) == 3
    assert all(c.id is not None for c in persisted)
    assert all(c.artifact_id == artifact.id for c in persisted)


def test_bulk_insert_preserves_chunk_order(db_session):
    artifact = _make_artifact(db_session)
    records = _make_chunks(5)
    persisted = chunk_store.bulk_insert(db_session, artifact.id, records)
    indices = [c.chunk_index for c in persisted]
    assert indices == list(range(5))


def test_get_by_artifact_returns_chunks_in_order(db_session):
    artifact = _make_artifact(db_session)
    chunk_store.bulk_insert(db_session, artifact.id, _make_chunks(4))
    retrieved = chunk_store.get_by_artifact(db_session, artifact.id)
    assert len(retrieved) == 4
    assert [c.chunk_index for c in retrieved] == [0, 1, 2, 3]


def test_get_by_artifact_returns_empty_for_unknown(db_session):
    result = chunk_store.get_by_artifact(db_session, "nonexistent-id")
    assert result == []


# ---------------------------------------------------------------------------
# delete_artifact: FK cascade + orphan handling
# ---------------------------------------------------------------------------

def test_delete_artifact_removes_chunks_via_cascade(db_session):
    artifact = _make_artifact(db_session)
    chunk_store.bulk_insert(db_session, artifact.id, _make_chunks(3))

    artifact_store.delete_artifact(db_session, artifact)

    remaining = chunk_store.get_by_artifact(db_session, artifact.id)
    assert remaining == []


def test_delete_artifact_orphans_child_versions(db_session):
    v1 = _make_artifact(db_session, filename="f.pdf")
    pr_v2 = _parse_result(filename="f.pdf")
    v2 = artifact_store.create_artifact(
        db_session, user_id="user1", parse_result=pr_v2,
        version_number=2, parent_id=v1.id,
    )
    assert v2.parent_id == v1.id

    artifact_store.delete_artifact(db_session, v1)

    db_session.expire(v2)
    db_session.refresh(v2)
    assert v2.parent_id is None  # orphaned, not deleted


def test_delete_artifact_removes_record(db_session):
    artifact = _make_artifact(db_session)
    artifact_id = artifact.id
    artifact_store.delete_artifact(db_session, artifact)
    assert artifact_store.get_artifact_by_id(db_session, artifact_id) is None


# ---------------------------------------------------------------------------
# delete_artifact: blob GC
# ---------------------------------------------------------------------------

def test_delete_artifact_removes_blob_when_last_reference(db_session, tmp_upload_dir):
    pr = _parse_result(file_type="pdf")
    artifact = artifact_store.create_artifact(db_session, user_id="user1", parse_result=pr)

    # Create the fake blob on disk
    blob_path = Path(str(tmp_upload_dir)) / f"{pr.file_hash}.pdf"
    blob_path.write_bytes(b"fake pdf bytes")
    assert blob_path.exists()

    blob_deleted = artifact_store.delete_artifact(db_session, artifact)

    assert blob_deleted is True
    assert not blob_path.exists()


def test_delete_artifact_keeps_blob_when_other_user_references_it(db_session, tmp_upload_dir):
    file_hash = "a" * 64
    pr = _parse_result(file_type="pdf", file_hash=file_hash)

    # Two users, same file hash
    a1 = artifact_store.create_artifact(db_session, user_id="user1", parse_result=pr)
    a2 = artifact_store.create_artifact(db_session, user_id="user2", parse_result=pr)

    blob_path = Path(str(tmp_upload_dir)) / f"{file_hash}.pdf"
    blob_path.write_bytes(b"shared content")

    # Delete user1's artifact — blob should be kept (user2 still references it)
    blob_deleted = artifact_store.delete_artifact(db_session, a1)

    assert blob_deleted is False
    assert blob_path.exists()


# ---------------------------------------------------------------------------
# list_artifacts: user scoping
# ---------------------------------------------------------------------------

def test_list_artifacts_scoped_to_user(db_session):
    _make_artifact(db_session, user_id="user1", filename="a.pdf")
    _make_artifact(db_session, user_id="user1", filename="b.pdf")
    _make_artifact(db_session, user_id="user2", filename="c.pdf")

    user1_artifacts = artifact_store.list_artifacts(db_session, "user1")
    assert len(user1_artifacts) == 2
    assert all(a.user_id == "user1" for a in user1_artifacts)


def test_list_artifacts_newest_first(db_session):
    import time
    a1 = _make_artifact(db_session, filename="first.pdf")
    time.sleep(0.01)
    a2 = _make_artifact(db_session, filename="second.pdf")

    artifacts = artifact_store.list_artifacts(db_session, "user1")
    assert artifacts[0].id == a2.id  # newest first
    assert artifacts[1].id == a1.id


def test_list_artifacts_empty_for_unknown_user(db_session):
    assert artifact_store.list_artifacts(db_session, "ghost") == []
