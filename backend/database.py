"""
SQLAlchemy engine, session factory, ORM base, and DB initialisation.

Design notes
------------
WAL mode (journal_mode=WAL):
    Write-Ahead Logging lets readers and the single writer run concurrently.
    Without WAL, a background embedding task holding a write lock would block
    every HTTP request that reads from the DB.

NullPool:
    SQLAlchemy's default QueuePool hands the *same* connection object back on
    every SessionLocal() call within a process.  SQLite WAL uses snapshot
    isolation — a pooled connection opened before a background-thread commit
    would keep reading the pre-commit snapshot forever, so the frontend would
    never see embedding_status change from "pending" to "ready".
    NullPool forces a brand-new OS connection each time, which always sees the
    latest committed WAL state.

FTS5 virtual table:
    ``chunks_fts`` is a content table pointing at ``chunks`` (content='chunks',
    content_rowid='rowid').  SQLite does not automatically update content tables,
    so three sync triggers (INSERT / DELETE / UPDATE) keep the FTS index in
    lockstep with the base table.
"""
import os
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import NullPool
from config import settings


def _get_engine():
    """
    Build and return a SQLAlchemy engine configured for the project's DB URL.

    For SQLite databases:
      - Creates the parent directory if it does not exist.
      - Sets check_same_thread=False so SQLAlchemy can share connections across
        threads (needed because FastAPI uses a thread pool for sync endpoints).
      - Uses NullPool to avoid WAL snapshot isolation issues (see module docs).
      - Enables WAL journal mode and foreign key enforcement via a connect event.

    For non-SQLite databases (Postgres, etc.):
      - No special configuration; the caller is responsible for connection pool
        tuning via environment variables or engine kwargs.

    Returns:
        sqlalchemy.engine.Engine
    """
    url = settings.database_url
    if url.startswith("sqlite:///"):
        # Strip the sqlite:/// prefix to get the filesystem path, then ensure
        # its parent directory exists so SQLite can create the file.
        path = url.replace("sqlite:///", "")
        if path and not path.startswith(":"):
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    # check_same_thread=False — required for SQLite when used with FastAPI's
    # thread pool.  The ORM session itself is not thread-safe; callers must not
    # share a single Session across threads.
    connect_args = {"check_same_thread": False} if "sqlite" in url else {}

    # NullPool: each SessionLocal() gets a brand-new connection so it always
    # reads the latest WAL-committed state.  Without this, SQLite WAL snapshot
    # isolation causes pooled connections to miss commits made by background
    # threads (e.g. the embedding task writing embedding_status = "ready").
    pool_kwargs = {"poolclass": NullPool} if "sqlite" in url else {}

    engine = create_engine(url, connect_args=connect_args, **pool_kwargs)

    if "sqlite" in url:
        # Attach pragmas to every new connection via the "connect" event.
        # This fires before any query, ensuring WAL and FK enforcement are
        # active even on connections opened by background threads.
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_conn, _):
            """Enable WAL mode and foreign-key constraints on every new SQLite connection."""
            cursor = dbapi_conn.cursor()
            # WAL mode: concurrent reads + one writer without blocking each other
            cursor.execute("PRAGMA journal_mode=WAL")
            # FK enforcement: SQLite disables foreign keys by default; this
            # ensures ON DELETE CASCADE on chunks works correctly.
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


# Module-level engine — created once at import time and reused for all sessions.
engine = _get_engine()

# Session factory — autocommit=False means all changes require an explicit
# db.commit().  autoflush=False prevents implicit SQL emission before queries.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """
    Declarative base for all SQLAlchemy ORM models.

    Every model class in models/ inherits from this Base.  SQLAlchemy uses it
    to track the metadata (tables, columns, indices) needed by create_all().
    """
    pass


# ---------------------------------------------------------------------------
# FTS5 virtual table + sync triggers
# ---------------------------------------------------------------------------
# Each statement is executed individually to avoid the naive semicolon-split
# that would break inside BEGIN…END trigger bodies.

_FTS5_STMTS = [
    # Content FTS5 table — mirrors the `text` column of `chunks`.
    # content='chunks' tells FTS5 to read the base table for full-document
    # retrieval; content_rowid='rowid' links the FTS rowid to the chunks rowid.
    """CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
       USING fts5(text, content='chunks', content_rowid='rowid')""",

    # INSERT trigger: add the new row's text to the FTS index immediately.
    """CREATE TRIGGER IF NOT EXISTS chunks_fts_insert
       AFTER INSERT ON chunks BEGIN
           INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
       END""",

    # DELETE trigger: remove the old row from the FTS index using the special
    # 'delete' command understood by FTS5 content tables.
    """CREATE TRIGGER IF NOT EXISTS chunks_fts_delete
       AFTER DELETE ON chunks BEGIN
           INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
       END""",

    # UPDATE trigger: delete the old text entry, then insert the new one.
    # This is the FTS5 way to handle an UPDATE — there is no native UPDATE cmd.
    """CREATE TRIGGER IF NOT EXISTS chunks_fts_update
       AFTER UPDATE ON chunks BEGIN
           INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
           INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
       END""",
]


def init_db() -> None:
    """
    Create all ORM tables, the FTS5 index, and the sync triggers.

    Also runs an additive migration to add the embedding_status column if it
    is missing (safe for databases created before that column was added).

    Safe to call multiple times — all DDL uses IF NOT EXISTS / IF NOT EXISTS.

    Side effects:
        - Imports all model modules so SQLAlchemy registers them with Base.metadata
          before calling create_all().  The ``# noqa: F401`` suppresses the
          "imported but unused" lint warning; the import is for side-effect only.
        - Executes each FTS5 DDL statement individually via a raw connection to
          avoid semicolon-splitting issues inside trigger bodies.
        - Commits the DDL transaction.
    """
    # noqa: F401 — imports register ORM models with Base.metadata
    from models import artifact, chunk, conversation, message  # noqa: F401

    # Create all ORM-declared tables (artifacts, chunks, conversations, messages).
    Base.metadata.create_all(bind=engine)

    with engine.connect() as conn:
        # Create FTS5 virtual table + three sync triggers individually
        for stmt in _FTS5_STMTS:
            conn.execute(text(stmt))

        # Additive migration: add embedding_status column if not present.
        # ALTER TABLE fails silently if the column already exists (the except
        # block is intentional — SQLite has no IF NOT EXISTS for ADD COLUMN).
        try:
            conn.execute(text(
                "ALTER TABLE artifacts ADD COLUMN embedding_status TEXT NOT NULL DEFAULT 'none'"
            ))
        except Exception:
            pass  # Column already exists — that is fine

        conn.commit()


def get_db():
    """
    FastAPI dependency that yields a SQLAlchemy Session for the duration of a request.

    Usage in endpoint:
        def my_route(db: Session = Depends(get_db)): ...

    The session is always closed in the finally block, even if the request raises
    an exception, preventing connection leaks.

    Yields:
        sqlalchemy.orm.Session: an open DB session bound to the current request.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
