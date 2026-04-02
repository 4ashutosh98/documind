"""
Shared fixtures for all test modules.

Design decisions:
  - Per-test SQLite file (NullPool) for full isolation and reliable WAL visibility
    between the request session and background-task sessions.
  - `patched_settings` disables all external service calls (Ollama, ChromaDB) so
    tests run without any running infrastructure.
  - `client` wires FastAPI's get_db dependency to the test session, then tears down
    cleanly after each test.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from fastapi.testclient import TestClient


@pytest.fixture()
def tmp_upload_dir(tmp_path):
    d = tmp_path / "uploads"
    d.mkdir()
    return d


@pytest.fixture()
def patched_settings(monkeypatch, tmp_upload_dir):
    """Override settings to disable all external service calls."""
    from config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_upload_dir))
    monkeypatch.setattr(settings, "enable_embeddings", False)
    monkeypatch.setattr(settings, "enable_contextual_enrichment", False)
    monkeypatch.setattr(settings, "enable_doc2query", False)
    monkeypatch.setattr(settings, "enable_query_rewriting", False)
    monkeypatch.setattr(settings, "enable_image_description", False)
    return settings


@pytest.fixture()
def db_session(monkeypatch, patched_settings, tmp_path):
    """
    Per-test isolated SQLite database.

    Patches `database.engine` and `database.SessionLocal` to a fresh file-based
    SQLite so every test starts clean. NullPool means each SessionLocal() call gets
    its own connection — background tasks can see data committed by the request
    handler without any WAL snapshot isolation issues.
    """
    import database

    db_path = tmp_path / "test.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(test_engine, "connect")
    def _set_pragmas(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    monkeypatch.setattr(database, "engine", test_engine)
    test_session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    monkeypatch.setattr(database, "SessionLocal", test_session_factory)

    database.init_db()

    session = test_session_factory()
    yield session
    session.close()


@pytest.fixture()
def client(db_session):
    """
    FastAPI TestClient with get_db overridden to use the isolated test session.

    Background tasks run synchronously within TestClient, so by the time a
    client call returns, any scheduled background work is already complete.
    """
    from main import app
    from database import get_db

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)
