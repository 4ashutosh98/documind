"""
Chunk persistence: bulk insert and retrieval.

Chunks are inserted in a single transaction for atomicity — if any row fails,
the whole batch is rolled back.  The FTS5 sync triggers in database.py
automatically update the ``chunks_fts`` index after each INSERT.
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy.orm import Session

from chunking.base import ChunkRecord
from models.chunk import Chunk


def bulk_insert(db: Session, artifact_id: str, chunk_records: list[ChunkRecord]) -> list[Chunk]:
    """
    Insert all chunks for an artifact in a single database transaction.

    After commit, the three FTS5 sync triggers (defined in database.py) have
    automatically indexed the text column of every new row into ``chunks_fts``,
    so keyword search is immediately available.

    Args:
        db:            SQLAlchemy session.
        artifact_id:   UUID of the parent artifact.
        chunk_records: Ordered list of ChunkRecord objects produced by a chunker.
                       Each record maps directly to one Chunk row.

    Returns:
        List of committed Chunk ORM instances (with auto-generated UUIDs).
        Parallel in order and length to chunk_records.
    """
    chunks = [
        Chunk(
            id=str(uuid.uuid4()),           # generate a fresh UUID for each chunk
            artifact_id=artifact_id,
            chunk_index=cr.chunk_index,
            text=cr.text,
            provenance=json.dumps(cr.provenance),   # serialise dict → JSON string
            chunk_type=cr.chunk_type,
            token_count=cr.token_count,
        )
        for cr in chunk_records
    ]
    db.add_all(chunks)
    db.commit()
    return chunks


def get_by_artifact(db: Session, artifact_id: str) -> list[Chunk]:
    """
    Retrieve all chunks for an artifact in chunk_index order.

    Used when displaying chunks in the artifact detail modal and when
    re-triggering embedding for an existing artifact.

    Args:
        db:          SQLAlchemy session.
        artifact_id: UUID of the parent artifact.

    Returns:
        Ordered list of Chunk ORM instances (ascending chunk_index).
        Empty list if the artifact has no chunks.
    """
    return (
        db.query(Chunk)
        .filter(Chunk.artifact_id == artifact_id)
        .order_by(Chunk.chunk_index)
        .all()
    )
