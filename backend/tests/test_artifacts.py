"""
Artifacts API tests: GET /artifacts, GET /artifacts/{id}, DELETE /artifacts/{id},
GET /artifacts/stream, POST /artifacts/{id}/reembed.

Key coverage:
  - embedding_status is correctly returned from the API (tests the fix in
    _serialize_artifact that was previously omitting this field)
  - DELETE cleans up vectors via delete_artifact_vectors (the fix in this session)
  - User isolation: one user cannot delete or see another user's artifacts
  - SSE: datetime fields are JSON-serializable (model_dump(mode="json") regression)
  - SSE: stream sends event: done when no pending artifacts
  - Re-embed: correct status transitions and guard conditions
"""
from __future__ import annotations

import hashlib
import io
import json
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import openpyxl
import pytest

from chunking.base import ChunkRecord
from ingestion.base import ParseResult
from models.artifact import Artifact
from storage import artifact_store, chunk_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_artifact(db_session, user_id="user1", filename="report.pdf", embedding_status="none"):
    pr = ParseResult(
        filename=filename,
        file_type="pdf",
        size_bytes=1024,
        file_hash=uuid.uuid4().hex * 2,
        extracted_metadata={"page_count": 2},
        markdown_content="# Section\n\nContent.",
    )
    artifact = artifact_store.create_artifact(db_session, user_id=user_id, parse_result=pr)
    if embedding_status != "none":
        db_session.query(Artifact).filter(Artifact.id == artifact.id).update(
            {"embedding_status": embedding_status}
        )
        db_session.commit()
        db_session.refresh(artifact)
    return artifact


def _create_chunks(db_session, artifact_id: str, count: int = 2):
    records = [
        ChunkRecord(
            text=f"Chunk text number {i}.",
            chunk_index=i,
            chunk_type="text",
            provenance={"section": "Section", "char_start": i * 50, "char_end": i * 50 + 30},
            token_count=4,
        )
        for i in range(count)
    ]
    return chunk_store.bulk_insert(db_session, artifact_id, records)


# ---------------------------------------------------------------------------
# GET /artifacts
# ---------------------------------------------------------------------------

def test_list_artifacts_empty(client):
    response = client.get("/artifacts?user_id=user1")
    assert response.status_code == 200
    assert response.json() == []


def test_list_artifacts_returns_only_requesting_user(client, db_session):
    _create_artifact(db_session, user_id="user1", filename="u1.pdf")
    _create_artifact(db_session, user_id="user2", filename="u2.pdf")

    response = client.get("/artifacts?user_id=user1")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["filename"] == "u1.pdf"


def test_list_artifacts_returns_embedding_status_field(client, db_session):
    """
    Regression test: _serialize_artifact was previously omitting embedding_status,
    causing Pydantic to always return the default 'none' regardless of DB state.
    """
    _create_artifact(db_session, embedding_status="ready")

    response = client.get("/artifacts?user_id=user1")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["embedding_status"] == "ready"


def test_list_artifacts_returns_all_statuses_correctly(client, db_session):
    _create_artifact(db_session, filename="a.pdf", embedding_status="none")
    _create_artifact(db_session, filename="b.pdf", embedding_status="pending")
    _create_artifact(db_session, filename="c.pdf", embedding_status="ready")

    response = client.get("/artifacts?user_id=user1")
    statuses = {a["filename"]: a["embedding_status"] for a in response.json()}
    assert statuses["a.pdf"] == "none"
    assert statuses["b.pdf"] == "pending"
    assert statuses["c.pdf"] == "ready"


def test_list_artifacts_missing_user_id_returns_422(client):
    response = client.get("/artifacts")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /artifacts/{id}
# ---------------------------------------------------------------------------

def test_get_artifact_returns_detail_with_chunks(client, db_session):
    artifact = _create_artifact(db_session)
    _create_chunks(db_session, artifact.id, count=3)

    response = client.get(f"/artifacts/{artifact.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == artifact.id
    assert len(data["chunks"]) == 3


def test_get_artifact_chunks_have_provenance(client, db_session):
    artifact = _create_artifact(db_session)
    _create_chunks(db_session, artifact.id, count=1)

    response = client.get(f"/artifacts/{artifact.id}")
    chunk = response.json()["chunks"][0]
    assert "provenance" in chunk
    assert chunk["provenance"]["section"] == "Section"


def test_get_artifact_not_found_returns_404(client):
    response = client.get(f"/artifacts/{uuid.uuid4()}")
    assert response.status_code == 404


def test_get_artifact_includes_extracted_metadata(client, db_session):
    artifact = _create_artifact(db_session)

    response = client.get(f"/artifacts/{artifact.id}")
    data = response.json()
    assert "extracted_metadata" in data
    assert data["extracted_metadata"].get("page_count") == 2


# ---------------------------------------------------------------------------
# DELETE /artifacts/{id}
# ---------------------------------------------------------------------------

def test_delete_artifact_returns_200(client, db_session):
    artifact = _create_artifact(db_session)

    response = client.delete(f"/artifacts/{artifact.id}?user_id=user1")
    assert response.status_code == 200
    assert response.json()["artifact_id"] == artifact.id


def test_delete_artifact_removes_from_db(client, db_session):
    artifact = _create_artifact(db_session)
    artifact_id = artifact.id

    client.delete(f"/artifacts/{artifact_id}?user_id=user1")

    db_session.expire_all()
    assert artifact_store.get_artifact_by_id(db_session, artifact_id) is None


def test_delete_artifact_wrong_user_returns_403(client, db_session):
    artifact = _create_artifact(db_session, user_id="user1")

    response = client.delete(f"/artifacts/{artifact.id}?user_id=user2")
    assert response.status_code == 403


def test_delete_artifact_not_found_returns_404(client):
    response = client.delete(f"/artifacts/{uuid.uuid4()}?user_id=user1")
    assert response.status_code == 404


def test_delete_artifact_calls_delete_artifact_vectors(client, db_session):
    """
    Regression test: DELETE /artifacts/{id} must call delete_artifact_vectors
    to clean up ChromaDB. Previously this was never called.
    """
    artifact = _create_artifact(db_session)

    with patch("api.artifacts.delete_artifact_vectors") as mock_delete_vectors:
        client.delete(f"/artifacts/{artifact.id}?user_id=user1")

    mock_delete_vectors.assert_called_once_with(artifact.id)


def test_delete_artifact_removes_blob_when_last_reference(client, db_session, tmp_upload_dir):
    pr = ParseResult(
        filename="sole.pdf",
        file_type="pdf",
        size_bytes=512,
        file_hash="e" * 64,
        extracted_metadata={},
    )
    artifact = artifact_store.create_artifact(db_session, user_id="user1", parse_result=pr)

    blob_path = Path(str(tmp_upload_dir)) / f"{'e' * 64}.pdf"
    blob_path.write_bytes(b"sole content")

    with patch("api.artifacts.delete_artifact_vectors"):
        response = client.delete(f"/artifacts/{artifact.id}?user_id=user1")

    assert response.json()["blob_deleted"] is True
    assert not blob_path.exists()


def test_delete_artifact_does_not_remove_blob_when_shared(client, db_session, tmp_upload_dir):
    shared_hash = "f" * 64
    pr1 = ParseResult(
        filename="a.pdf", file_type="pdf", size_bytes=512,
        file_hash=shared_hash, extracted_metadata={},
    )
    pr2 = ParseResult(
        filename="b.pdf", file_type="pdf", size_bytes=512,
        file_hash=shared_hash, extracted_metadata={},
    )
    a1 = artifact_store.create_artifact(db_session, user_id="user1", parse_result=pr1)
    artifact_store.create_artifact(db_session, user_id="user2", parse_result=pr2)

    blob_path = Path(str(tmp_upload_dir)) / f"{shared_hash}.pdf"
    blob_path.write_bytes(b"shared")

    with patch("api.artifacts.delete_artifact_vectors"):
        response = client.delete(f"/artifacts/{a1.id}?user_id=user1")

    assert response.json()["blob_deleted"] is False
    assert blob_path.exists()


def test_delete_artifact_cascades_chunks_deleted(client, db_session):
    artifact = _create_artifact(db_session)
    _create_chunks(db_session, artifact.id, count=5)
    artifact_id = artifact.id

    with patch("api.artifacts.delete_artifact_vectors"):
        client.delete(f"/artifacts/{artifact_id}?user_id=user1")

    db_session.expire_all()
    remaining = chunk_store.get_by_artifact(db_session, artifact_id)
    assert remaining == []


# ---------------------------------------------------------------------------
# SSE: GET /artifacts/stream
# ---------------------------------------------------------------------------

class _NonClosingSession:
    """Proxy that prevents close() from shutting down the shared test session."""
    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        pass  # Do not close the underlying test session


def test_serialize_artifact_model_dump_is_json_serializable(db_session):
    """
    Regression: model_dump(mode='json') must return ISO strings, not datetime objects.

    Before the fix, model_dump() returned Python datetime objects which
    json.dumps() could not serialize, crashing the SSE endpoint.
    """
    import json as _json
    from api.artifacts import _serialize_artifact

    artifact = _create_artifact(db_session)
    summary = _serialize_artifact(artifact)
    dumped = summary.model_dump(mode="json")

    # Should not raise TypeError
    json_str = _json.dumps(dumped)
    data = _json.loads(json_str)

    assert data["id"] == artifact.id
    # Datetime fields must be strings (ISO format), not raw datetime objects
    assert isinstance(data["upload_timestamp"], str)
    assert isinstance(data["first_seen"], str)
    assert isinstance(data["last_seen"], str)


def test_sse_stream_returns_event_stream_content_type(client, db_session, monkeypatch):
    """SSE endpoint must respond with text/event-stream."""
    monkeypatch.setattr(
        "api.artifacts.SessionLocal",
        lambda: _NonClosingSession(db_session),
    )
    response = client.get("/artifacts/stream?user_id=user1")
    assert "text/event-stream" in response.headers["content-type"]


def test_sse_stream_sends_done_when_no_artifacts(client, db_session, monkeypatch):
    """Stream must send event: done immediately when the user has no artifacts."""
    monkeypatch.setattr(
        "api.artifacts.SessionLocal",
        lambda: _NonClosingSession(db_session),
    )
    response = client.get("/artifacts/stream?user_id=user1")
    assert "event: done" in response.text


def test_sse_stream_sends_done_when_all_artifacts_ready(client, db_session, monkeypatch):
    """Stream must send event: done when no artifact has embedding_status='pending'."""
    monkeypatch.setattr(
        "api.artifacts.SessionLocal",
        lambda: _NonClosingSession(db_session),
    )
    _create_artifact(db_session, embedding_status="ready")
    _create_artifact(db_session, filename="b.pdf", embedding_status="none")

    response = client.get("/artifacts/stream?user_id=user1")
    assert "event: done" in response.text


def test_sse_stream_data_is_valid_json(client, db_session, monkeypatch):
    """Each data event payload must be a valid JSON array of artifact summaries."""
    monkeypatch.setattr(
        "api.artifacts.SessionLocal",
        lambda: _NonClosingSession(db_session),
    )
    _create_artifact(db_session, embedding_status="ready")

    response = client.get("/artifacts/stream?user_id=user1")
    # Find a data: line and parse it
    for line in response.text.splitlines():
        if line.startswith("data:"):
            payload = json.loads(line[len("data:"):].strip())
            assert isinstance(payload, list)
            assert payload[0]["id"] is not None
            # Timestamps must be strings (not datetime objects)
            assert isinstance(payload[0]["upload_timestamp"], str)
            break


def test_sse_stream_missing_user_id_returns_422(client):
    response = client.get("/artifacts/stream")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Re-embed: POST /artifacts/{id}/reembed
# ---------------------------------------------------------------------------

def test_reembed_returns_pending_status(client, db_session):
    artifact = _create_artifact(db_session)
    _create_chunks(db_session, artifact.id, count=2)

    with patch("api.artifacts._reembed_in_background"):
        response = client.post(f"/artifacts/{artifact.id}/reembed?user_id=user1")

    assert response.status_code == 200
    data = response.json()
    assert data["embedding_status"] == "pending"
    assert data["artifact_id"] == artifact.id


def test_reembed_sets_embedding_status_pending_synchronously(client, db_session):
    """Status must be 'pending' in the DB before background task runs."""
    artifact = _create_artifact(db_session)
    _create_chunks(db_session, artifact.id, count=2)

    with patch("api.artifacts._reembed_in_background"):
        client.post(f"/artifacts/{artifact.id}/reembed?user_id=user1")

    db_session.expire_all()
    from models.artifact import Artifact as ArtifactModel
    updated = db_session.query(ArtifactModel).filter(ArtifactModel.id == artifact.id).first()
    assert updated.embedding_status == "pending"


def test_reembed_not_found_returns_404(client):
    response = client.post(f"/artifacts/{uuid.uuid4()}/reembed?user_id=user1")
    assert response.status_code == 404


def test_reembed_wrong_user_returns_403(client, db_session):
    artifact = _create_artifact(db_session, user_id="user1")

    response = client.post(f"/artifacts/{artifact.id}/reembed?user_id=user2")
    assert response.status_code == 403


def test_reembed_no_chunks_returns_422(client, db_session):
    """Re-embed requires existing chunks; 422 if there are none (re-upload required)."""
    artifact = _create_artifact(db_session)
    # No chunks created

    response = client.post(f"/artifacts/{artifact.id}/reembed?user_id=user1")
    assert response.status_code == 422


def test_reembed_already_pending_returns_early(client, db_session):
    """If embedding_status is already 'pending', return immediately without scheduling."""
    artifact = _create_artifact(db_session, embedding_status="pending")
    _create_chunks(db_session, artifact.id, count=2)

    with patch("api.artifacts._reembed_in_background") as mock_bg:
        response = client.post(f"/artifacts/{artifact.id}/reembed?user_id=user1")

    assert response.status_code == 200
    assert response.json()["embedding_status"] == "pending"
    assert "Already in progress" in response.json()["message"]
    mock_bg.assert_not_called()


def test_reembed_schedules_background_task(client, db_session):
    """Background task must be scheduled with the correct artifact_id."""
    artifact = _create_artifact(db_session)
    _create_chunks(db_session, artifact.id, count=2)

    with patch("api.artifacts._reembed_in_background") as mock_bg:
        client.post(f"/artifacts/{artifact.id}/reembed?user_id=user1")

    mock_bg.assert_called_once()
    call_kwargs = mock_bg.call_args.kwargs
    assert call_kwargs["artifact_id"] == artifact.id
    assert call_kwargs["user_id"] == "user1"
