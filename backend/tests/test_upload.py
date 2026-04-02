"""
Upload pipeline integration tests.

Tests the full POST /upload flow including:
  - Deduplication (exact duplicate → return existing)
  - Versioning (same filename, different content → new version)
  - Cross-user metadata reuse (same file hash, different user → skip re-parse)
  - Unsupported file type rejection
  - XLSX ingestion with real file bytes (no parser mock needed — pandas handles it)
  - Embedding status lifecycle

parse_file is mocked for PDF/DOCX to avoid requiring Docling + ML models in CI.
XLSX uses the real XlsxParser (pandas), which has no external dependencies.
"""
from __future__ import annotations

import hashlib
import io
import json
import uuid
from unittest.mock import patch

import openpyxl
import pytest

from ingestion.base import ParseResult
from models.artifact import Artifact
from storage import artifact_store, chunk_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_parse_result(filename="test.pdf", file_hash=None, content="# Intro\n\nContent here."):
    """Return a ParseResult for use in mocked parse_file calls."""
    return ParseResult(
        filename=filename,
        file_type="pdf",
        size_bytes=100,
        file_hash=file_hash or uuid.uuid4().hex * 2,
        extracted_metadata={"page_count": 1, "title": "Test"},
        markdown_content=content,
    )


def _pdf_bytes(content: bytes = b"%PDF-1.4 fake content") -> bytes:
    return content


def _xlsx_bytes(data: dict[str, list[list]]) -> bytes:
    """Generate a minimal real XLSX file in memory."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet_name, rows in data.items():
        ws = wb.create_sheet(sheet_name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Unsupported file types
# ---------------------------------------------------------------------------

def test_upload_unsupported_extension_returns_415(client):
    response = client.post(
        "/upload",
        files={"file": ("document.txt", b"plain text content", "text/plain")},
        data={"user_id": "user1"},
    )
    assert response.status_code == 415
    assert ".txt" in response.json()["detail"]


def test_upload_missing_user_id_returns_422(client):
    response = client.post(
        "/upload",
        files={"file": ("report.pdf", b"fake", "application/pdf")},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# New file upload (PDF, mocked parser)
# ---------------------------------------------------------------------------

def test_upload_new_pdf_returns_201_created(client, db_session):
    fake_bytes = _pdf_bytes()
    fake_hash = hashlib.sha256(fake_bytes).hexdigest()
    pr = _fake_parse_result(file_hash=fake_hash)

    with patch("api.upload.parse_file", return_value=pr):
        response = client.post(
            "/upload",
            files={"file": ("report.pdf", fake_bytes, "application/pdf")},
            data={"user_id": "user1"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "created"
    assert body["version_number"] == 1
    assert "artifact_id" in body


def test_upload_new_pdf_persists_chunks(client, db_session):
    fake_bytes = _pdf_bytes()
    fake_hash = hashlib.sha256(fake_bytes).hexdigest()
    pr = _fake_parse_result(
        file_hash=fake_hash,
        content="# Section 1\n\nRevenue grew significantly.\n\n# Section 2\n\nExpenses were stable.",
    )

    with patch("api.upload.parse_file", return_value=pr):
        response = client.post(
            "/upload",
            files={"file": ("report.pdf", fake_bytes, "application/pdf")},
            data={"user_id": "user1"},
        )

    artifact_id = response.json()["artifact_id"]
    chunks = chunk_store.get_by_artifact(db_session, artifact_id)
    assert len(chunks) >= 1
    combined_text = " ".join(c.text for c in chunks)
    assert "revenue" in combined_text.lower() or "section" in combined_text.lower()


def test_upload_creates_blob_on_disk(client, db_session, tmp_upload_dir):
    fake_bytes = _pdf_bytes(b"unique content for blob test")
    fake_hash = hashlib.sha256(fake_bytes).hexdigest()
    pr = _fake_parse_result(file_hash=fake_hash)

    with patch("api.upload.parse_file", return_value=pr):
        client.post(
            "/upload",
            files={"file": ("report.pdf", fake_bytes, "application/pdf")},
            data={"user_id": "user1"},
        )

    from pathlib import Path
    blob = Path(str(tmp_upload_dir)) / f"{fake_hash}.pdf"
    assert blob.exists()


# ---------------------------------------------------------------------------
# Exact duplicate
# ---------------------------------------------------------------------------

def test_upload_exact_duplicate_returns_duplicate_status(client, db_session):
    fake_bytes = _pdf_bytes()
    fake_hash = hashlib.sha256(fake_bytes).hexdigest()
    pr = _fake_parse_result(file_hash=fake_hash)

    with patch("api.upload.parse_file", return_value=pr):
        r1 = client.post(
            "/upload",
            files={"file": ("report.pdf", fake_bytes, "application/pdf")},
            data={"user_id": "user1"},
        )
        r2 = client.post(
            "/upload",
            files={"file": ("report.pdf", fake_bytes, "application/pdf")},
            data={"user_id": "user1"},
        )

    assert r1.json()["status"] == "created"
    assert r2.json()["status"] == "duplicate"
    assert r2.json()["artifact_id"] == r1.json()["artifact_id"]


def test_upload_duplicate_does_not_create_new_artifact(client, db_session):
    fake_bytes = _pdf_bytes(b"same content")
    fake_hash = hashlib.sha256(fake_bytes).hexdigest()
    pr = _fake_parse_result(file_hash=fake_hash)

    with patch("api.upload.parse_file", return_value=pr):
        client.post("/upload", files={"file": ("r.pdf", fake_bytes, "application/pdf")}, data={"user_id": "user1"})
        client.post("/upload", files={"file": ("r.pdf", fake_bytes, "application/pdf")}, data={"user_id": "user1"})

    artifacts = artifact_store.list_artifacts(db_session, "user1")
    assert len(artifacts) == 1


# ---------------------------------------------------------------------------
# Version chain
# ---------------------------------------------------------------------------

def test_upload_new_version_increments_version_number(client, db_session):
    v1_bytes = _pdf_bytes(b"version 1 content")
    v2_bytes = _pdf_bytes(b"version 2 content - different")
    v1_hash = hashlib.sha256(v1_bytes).hexdigest()
    v2_hash = hashlib.sha256(v2_bytes).hexdigest()

    pr_v1 = _fake_parse_result(filename="report.pdf", file_hash=v1_hash)
    pr_v2 = _fake_parse_result(filename="report.pdf", file_hash=v2_hash)

    with patch("api.upload.parse_file", return_value=pr_v1):
        r1 = client.post("/upload", files={"file": ("report.pdf", v1_bytes, "application/pdf")}, data={"user_id": "user1"})
    with patch("api.upload.parse_file", return_value=pr_v2):
        r2 = client.post("/upload", files={"file": ("report.pdf", v2_bytes, "application/pdf")}, data={"user_id": "user1"})

    assert r1.json()["version_number"] == 1
    assert r2.json()["version_number"] == 2
    assert r2.json()["status"] == "new_version"


def test_upload_new_version_sets_parent_id(client, db_session):
    v1_bytes = _pdf_bytes(b"v1 content")
    v2_bytes = _pdf_bytes(b"v2 content")
    v1_hash = hashlib.sha256(v1_bytes).hexdigest()
    v2_hash = hashlib.sha256(v2_bytes).hexdigest()

    pr_v1 = _fake_parse_result(filename="report.pdf", file_hash=v1_hash)
    pr_v2 = _fake_parse_result(filename="report.pdf", file_hash=v2_hash)

    with patch("api.upload.parse_file", return_value=pr_v1):
        r1 = client.post("/upload", files={"file": ("report.pdf", v1_bytes, "application/pdf")}, data={"user_id": "user1"})
    with patch("api.upload.parse_file", return_value=pr_v2):
        r2 = client.post("/upload", files={"file": ("report.pdf", v2_bytes, "application/pdf")}, data={"user_id": "user1"})

    v2 = artifact_store.get_artifact_by_id(db_session, r2.json()["artifact_id"])
    assert v2.parent_id == r1.json()["artifact_id"]


# ---------------------------------------------------------------------------
# Cross-user metadata reuse
# ---------------------------------------------------------------------------

def test_upload_reuses_metadata_for_same_hash_different_user(client, db_session):
    """Second user uploading same file should skip re-parsing (metadata copied)."""
    shared_bytes = _pdf_bytes(b"shared document content")
    shared_hash = hashlib.sha256(shared_bytes).hexdigest()
    pr = _fake_parse_result(file_hash=shared_hash, filename="shared.pdf")

    with patch("api.upload.parse_file", return_value=pr) as mock_parse:
        client.post("/upload", files={"file": ("shared.pdf", shared_bytes, "application/pdf")}, data={"user_id": "user1"})
        first_call_count = mock_parse.call_count

        client.post("/upload", files={"file": ("shared.pdf", shared_bytes, "application/pdf")}, data={"user_id": "user2"})
        second_call_count = mock_parse.call_count

    # parse_file should be called once (for user1) but NOT again for user2
    assert first_call_count == 1
    assert second_call_count == 1  # no additional call for user2


# ---------------------------------------------------------------------------
# XLSX upload (real file, no mock)
# ---------------------------------------------------------------------------

def test_upload_xlsx_creates_table_row_chunks(client, db_session):
    xlsx_data = _xlsx_bytes({
        "Revenue": [
            ["Quarter", "Revenue", "Growth"],
            ["Q1", "5B", "20%"],
            ["Q2", "6B", "22%"],
            ["Q3", "7B", "18%"],
        ]
    })

    response = client.post(
        "/upload",
        files={"file": ("financials.xlsx", xlsx_data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"user_id": "user1"},
    )

    assert response.status_code == 201
    artifact_id = response.json()["artifact_id"]
    chunks = chunk_store.get_by_artifact(db_session, artifact_id)
    assert len(chunks) >= 1
    assert all(c.chunk_type == "table_row" for c in chunks)

    provenance = json.loads(chunks[0].provenance)
    assert provenance.get("sheet") == "Revenue"


def test_upload_xlsx_metadata_includes_sheet_info(client, db_session):
    xlsx_data = _xlsx_bytes({
        "Sheet1": [["Name", "Value"], ["A", "1"]],
        "Sheet2": [["X", "Y"], ["B", "2"]],
    })

    response = client.post(
        "/upload",
        files={"file": ("data.xlsx", xlsx_data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"user_id": "user1"},
    )

    artifact_id = response.json()["artifact_id"]
    artifact = artifact_store.get_artifact_by_id(db_session, artifact_id)
    metadata = json.loads(artifact.extracted_metadata)
    assert "Sheet1" in metadata.get("sheet_names", [])
    assert "Sheet2" in metadata.get("sheet_names", [])


# ---------------------------------------------------------------------------
# Embedding status
# ---------------------------------------------------------------------------

def test_upload_embedding_status_none_when_disabled(client, db_session, patched_settings):
    patched_settings.enable_embeddings = False
    fake_bytes = _pdf_bytes(b"content a")
    pr = _fake_parse_result(file_hash=hashlib.sha256(fake_bytes).hexdigest())

    with patch("api.upload.parse_file", return_value=pr):
        response = client.post("/upload", files={"file": ("a.pdf", fake_bytes, "application/pdf")}, data={"user_id": "user1"})

    artifact = artifact_store.get_artifact_by_id(db_session, response.json()["artifact_id"])
    assert artifact.embedding_status == "none"


def test_upload_duplicate_retriggers_embedding_when_status_is_none(client, db_session, patched_settings):
    """
    When a user re-uploads a duplicate and the artifact has embedding_status='none'
    and embeddings are enabled, a background task should be scheduled.
    We verify the status changes to 'pending'.
    """
    patched_settings.enable_embeddings = True

    fake_bytes = _pdf_bytes(b"content b")
    fake_hash = hashlib.sha256(fake_bytes).hexdigest()
    pr = _fake_parse_result(file_hash=fake_hash)

    with patch("api.upload.parse_file", return_value=pr):
        r1 = client.post("/upload", files={"file": ("b.pdf", fake_bytes, "application/pdf")}, data={"user_id": "user1"})

    artifact_id = r1.json()["artifact_id"]

    # Manually reset to "none" (simulating state before embeddings were enabled)
    db_session.query(Artifact).filter(Artifact.id == artifact_id).update({"embedding_status": "none"})
    db_session.commit()

    # Re-upload the same file — should trigger embedding pipeline
    with patch("api.upload.parse_file", return_value=pr):
        with patch("api.upload.embed_and_index", return_value=0) as mock_embed:
            r2 = client.post("/upload", files={"file": ("b.pdf", fake_bytes, "application/pdf")}, data={"user_id": "user1"})

    assert r2.json()["status"] == "duplicate"
    db_session.expire_all()
    artifact = artifact_store.get_artifact_by_id(db_session, artifact_id)
    # Background task ran (embed_and_index returned 0) → status stays "pending" (honest state)
    assert artifact.embedding_status == "pending"
