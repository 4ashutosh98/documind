"""
Dev/testing utilities — POST /dev/reset.

Wipes all persistent state: SQLite rows, uploaded blobs on disk, and ChromaDB
vector collections.  Intended for local development and demo resets only.

In production this endpoint should be removed or protected behind an admin
auth check.  Currently it is unprotected because the system uses mock auth.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from config import settings
from database import get_db

router = APIRouter(prefix="/dev", tags=["dev"])


@router.post("/reset", status_code=200)
def reset_all_data(db: Session = Depends(get_db)) -> dict:
    """
    Nuke all stored data — useful for demo resets and test teardowns.

    Wipes in this order:
      1. SQLite rows (FK-safe order: messages → conversations → chunks → artifacts)
         The FTS5 DELETE trigger fires for each chunk row deletion, keeping the
         FTS index consistent (empty after reset).
      2. All uploaded blobs under settings.upload_dir (shutil.rmtree, then mkdir).
      3. ChromaDB persist directory (shutil.rmtree).
      4. In-process ChromaDB singletons reset to None so the next request
         creates fresh Chroma collections rather than re-using the old ones.

    Args:
        db: SQLAlchemy session (injected by FastAPI).

    Returns:
        {"status": "reset", "message": "All data wiped."}
    """
    # 1. SQLite — delete in FK-safe order (children before parents)
    # messages references conversations; chunks references artifacts
    db.execute(text("DELETE FROM messages"))
    db.execute(text("DELETE FROM conversations"))
    db.execute(text("DELETE FROM chunks"))
    db.execute(text("DELETE FROM artifacts"))
    db.commit()

    # 2. Uploaded blobs — delete the directory then recreate it empty
    upload_dir = Path(settings.upload_dir)
    if upload_dir.exists():
        shutil.rmtree(upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    # 3. ChromaDB — delete the entire persist directory
    # All vector collections live under this single directory
    chroma_dir = Path(settings.chroma_dir)
    if chroma_dir.exists():
        shutil.rmtree(chroma_dir)

    # 4. Reset in-process ChromaDB singletons so the next upsert/query call
    # creates new Chroma collections pointing at the now-empty persist directory
    try:
        import storage.vector_store as vs
        vs._chunks_store = None
        vs._questions_store = None
    except Exception:
        pass  # non-fatal if vector_store was never imported (e.g. embeddings disabled)

    return {"status": "reset", "message": "All data wiped."}
