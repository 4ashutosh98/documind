"""
Retrieval layer tests: FTS5 keyword search, RRF fusion, result formatting.

Uses a real in-memory SQLite with FTS5 triggers — no mocks for the DB layer.
Semantic search and Ollama calls are mocked since they require a running Ollama instance.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from chunking.base import ChunkRecord
from ingestion.base import ParseResult
from retrieval.hybrid_search import _rrf_merge, _ready_artifact_ids, search as hybrid_search
from retrieval.keyword_search import _build_fts_query, _compute_match_positions, search as fts_search
from retrieval.result_formatter import format_results
from storage import artifact_store, chunk_store


# ---------------------------------------------------------------------------
# Helpers: seed the DB with artifacts + searchable chunks
# ---------------------------------------------------------------------------

def _seed_artifact(db, user_id="user1", filename="report.pdf", file_hash=None):
    pr = ParseResult(
        filename=filename,
        file_type="pdf",
        size_bytes=1024,
        file_hash=file_hash or uuid.uuid4().hex * 2,
        extracted_metadata={"page_count": 2},
        markdown_content="",
    )
    return artifact_store.create_artifact(db, user_id=user_id, parse_result=pr)


def _seed_chunks(db, artifact_id: str, texts: list[str]) -> list:
    records = [
        ChunkRecord(
            text=text,
            chunk_index=i,
            chunk_type="text",
            provenance={"section": "Test", "char_start": i * 200, "char_end": i * 200 + 100},
            token_count=len(text.split()),
        )
        for i, text in enumerate(texts)
    ]
    return chunk_store.bulk_insert(db, artifact_id, records)


def _raw_row(chunk_id, artifact_id, text, user_id="user1", score=1.0):
    """Build a minimal raw result dict matching what search() returns."""
    return {
        "chunk_id": chunk_id,
        "artifact_id": artifact_id,
        "chunk_index": 0,
        "chunk_text": text,
        "chunk_type": "text",
        "provenance": json.dumps({"section": "A", "char_start": 0, "char_end": len(text)}),
        "token_count": len(text.split()),
        "user_id": user_id,
        "filename": "report.pdf",
        "file_type": "pdf",
        "size_bytes": 1024,
        "file_hash": "a" * 64,
        "version_number": 1,
        "parent_id": None,
        "uploaded_by": user_id,
        "upload_timestamp": datetime.now(timezone.utc),
        "first_seen": datetime.now(timezone.utc),
        "last_seen": datetime.now(timezone.utc),
        "extracted_metadata": "{}",
        "score": score,
        "match_positions": [],
    }


# ---------------------------------------------------------------------------
# FTS5 keyword search
# ---------------------------------------------------------------------------

def test_keyword_search_finds_matching_chunk(db_session):
    artifact = _seed_artifact(db_session)
    _seed_chunks(db_session, artifact.id, [
        "Revenue grew 25% year over year to five billion dollars.",
        "Operating expenses remained stable.",
    ])
    results = fts_search(db_session, "revenue", "user1")
    assert len(results) >= 1
    assert any("revenue" in r["chunk_text"].lower() for r in results)


def test_keyword_search_multi_term_or_semantics(db_session):
    artifact = _seed_artifact(db_session)
    _seed_chunks(db_session, artifact.id, [
        "Profit margin expanded significantly.",
        "Revenue growth exceeded expectations.",
        "Operational efficiency improved.",
    ])
    # Both "profit" and "revenue" should appear in results
    results = fts_search(db_session, "profit revenue", "user1")
    texts = [r["chunk_text"].lower() for r in results]
    assert any("profit" in t for t in texts)
    assert any("revenue" in t for t in texts)


def test_keyword_search_user_isolation(db_session):
    a1 = _seed_artifact(db_session, user_id="user1")
    a2 = _seed_artifact(db_session, user_id="user2")
    _seed_chunks(db_session, a1.id, ["Revenue data for user one."])
    _seed_chunks(db_session, a2.id, ["Revenue data for user two."])

    u1_results = fts_search(db_session, "revenue", "user1")
    u2_results = fts_search(db_session, "revenue", "user2")

    assert all(r["user_id"] == "user1" for r in u1_results)
    assert all(r["user_id"] == "user2" for r in u2_results)


def test_keyword_search_artifact_id_filter(db_session):
    a1 = _seed_artifact(db_session, filename="a.pdf")
    a2 = _seed_artifact(db_session, filename="b.pdf")
    _seed_chunks(db_session, a1.id, ["Revenue from Document A."])
    _seed_chunks(db_session, a2.id, ["Revenue from Document B."])

    results = fts_search(db_session, "revenue", "user1", artifact_ids=[a1.id])
    assert len(results) >= 1
    assert all(r["artifact_id"] == a1.id for r in results)


def test_keyword_search_returns_empty_for_no_match(db_session):
    artifact = _seed_artifact(db_session)
    _seed_chunks(db_session, artifact.id, ["Unrelated agricultural content."])
    results = fts_search(db_session, "quantum semiconductor revenue", "user1")
    assert results == []


def test_keyword_search_returns_empty_for_no_chunks(db_session):
    _seed_artifact(db_session)  # artifact with no chunks
    results = fts_search(db_session, "revenue", "user1")
    assert results == []


def test_keyword_search_includes_match_positions(db_session):
    artifact = _seed_artifact(db_session)
    _seed_chunks(db_session, artifact.id, ["Revenue grew and revenue shrank."])
    results = fts_search(db_session, "revenue", "user1")
    assert len(results) >= 1
    positions = results[0]["match_positions"]
    assert len(positions) >= 2  # "revenue" appears twice


def test_keyword_search_respects_limit(db_session):
    artifact = _seed_artifact(db_session)
    _seed_chunks(db_session, artifact.id, [f"Revenue chunk {i}." for i in range(10)])
    results = fts_search(db_session, "revenue", "user1", limit=3)
    assert len(results) <= 3


# ---------------------------------------------------------------------------
# RRF merge
# ---------------------------------------------------------------------------

def test_rrf_merge_scores_correctly():
    """RRF formula: 1/(60+rank). Two lists agreeing on a doc → higher combined score."""
    cid = "chunk-1"
    row = _raw_row(cid, "art-1", "text")
    fts = [row]
    sem = [row]
    merged = _rrf_merge([fts, sem], limit=10)
    assert len(merged) == 1
    # Score from rank 1 in two lists = 2 * (1/61) ≈ 0.0328
    assert merged[0]["score"] > 1 / 61


def test_rrf_merge_deduplicates_by_chunk_id():
    cid = "chunk-1"
    row = _raw_row(cid, "art-1", "text", score=1.0)
    # Same chunk appears in both lists — should appear once in output
    merged = _rrf_merge([[row], [row]], limit=10)
    assert len(merged) == 1


def test_rrf_merge_respects_limit():
    rows = [_raw_row(f"c{i}", "art-1", f"text {i}") for i in range(10)]
    merged = _rrf_merge([rows], limit=3)
    assert len(merged) == 3


def test_rrf_merge_higher_rank_gives_higher_score():
    r1 = _raw_row("c1", "art-1", "text")
    r2 = _raw_row("c2", "art-1", "text")
    r3 = _raw_row("c3", "art-1", "text")
    # r1 is rank 1, r3 is rank 3 — r1 should score higher
    merged = _rrf_merge([[r1, r2, r3]], limit=3)
    scores = [m["score"] for m in merged]
    assert scores[0] >= scores[1] >= scores[2]


def test_rrf_merge_empty_lists():
    assert _rrf_merge([], limit=10) == []
    assert _rrf_merge([[]], limit=10) == []


# ---------------------------------------------------------------------------
# _ready_artifact_ids
# ---------------------------------------------------------------------------

def test_ready_artifact_ids_filters_by_status(db_session):
    a_none = _seed_artifact(db_session, filename="a.pdf")
    a_pending = _seed_artifact(db_session, filename="b.pdf")
    a_ready = _seed_artifact(db_session, filename="c.pdf")

    # Set embedding statuses
    from models.artifact import Artifact
    db_session.query(Artifact).filter(Artifact.id == a_pending.id).update(
        {"embedding_status": "pending"}
    )
    db_session.query(Artifact).filter(Artifact.id == a_ready.id).update(
        {"embedding_status": "ready"}
    )
    db_session.commit()

    ready = _ready_artifact_ids(db_session, "user1", artifact_ids=None)
    assert a_ready.id in ready
    assert a_none.id not in ready
    assert a_pending.id not in ready


def test_ready_artifact_ids_scoped_to_user(db_session):
    a1 = _seed_artifact(db_session, user_id="user1", filename="a.pdf")
    a2 = _seed_artifact(db_session, user_id="user2", filename="b.pdf")

    from models.artifact import Artifact
    for a in [a1, a2]:
        db_session.query(Artifact).filter(Artifact.id == a.id).update(
            {"embedding_status": "ready"}
        )
    db_session.commit()

    ready = _ready_artifact_ids(db_session, "user1", artifact_ids=None)
    assert a1.id in ready
    assert a2.id not in ready


# ---------------------------------------------------------------------------
# hybrid_search: fallback behavior when embeddings disabled
# ---------------------------------------------------------------------------

def test_hybrid_search_falls_back_to_fts_when_embeddings_disabled(db_session, patched_settings):
    patched_settings.enable_embeddings = False
    artifact = _seed_artifact(db_session)
    _seed_chunks(db_session, artifact.id, ["Revenue data in this chunk."])

    results = hybrid_search(db_session, "revenue", "user1")
    assert len(results) >= 1


def test_hybrid_search_falls_back_to_fts_when_no_ready_artifacts(db_session, patched_settings):
    patched_settings.enable_embeddings = True
    artifact = _seed_artifact(db_session)
    _seed_chunks(db_session, artifact.id, ["Revenue data here."])
    # embedding_status is "none" by default — no ready artifacts

    with patch("retrieval.hybrid_search.sem_search") as mock_sem:
        results = hybrid_search(db_session, "revenue", "user1")

    mock_sem.assert_not_called()  # semantic search skipped — no ready artifacts
    assert len(results) >= 1


def test_hybrid_search_uses_rrf_when_embeddings_ready(db_session, patched_settings):
    patched_settings.enable_embeddings = True
    artifact = _seed_artifact(db_session)
    _seed_chunks(db_session, artifact.id, ["Revenue quarter results."])

    # Mark artifact as ready
    from models.artifact import Artifact
    db_session.query(Artifact).filter(Artifact.id == artifact.id).update(
        {"embedding_status": "ready"}
    )
    db_session.commit()

    chunk_id = chunk_store.get_by_artifact(db_session, artifact.id)[0].id
    sem_row = _raw_row(chunk_id, artifact.id, "Revenue quarter results.")

    with patch("retrieval.hybrid_search.sem_search", return_value=[sem_row]):
        results = hybrid_search(db_session, "revenue", "user1")

    assert len(results) >= 1


# ---------------------------------------------------------------------------
# result_formatter
# ---------------------------------------------------------------------------

def test_result_formatter_produces_correct_schema():
    chunk_id = str(uuid.uuid4())
    artifact_id = str(uuid.uuid4())
    raw = [_raw_row(chunk_id, artifact_id, "Some chunk text about revenue.")]

    response = format_results("revenue", raw)

    assert response.query == "revenue"
    assert response.total == 1
    match = response.results[0]
    assert match.chunk.id == chunk_id
    assert match.artifact.id == artifact_id
    assert match.chunk.text == "Some chunk text about revenue."


def test_result_formatter_handles_empty_results():
    response = format_results("anything", [])
    assert response.total == 0
    assert response.results == []


def test_result_formatter_preserves_match_positions():
    raw = [_raw_row("c1", "a1", "Revenue and profit.")]
    raw[0]["match_positions"] = [(0, 7), (12, 18)]
    response = format_results("revenue profit", raw)
    assert response.results[0].match_positions == [(0, 7), (12, 18)]
