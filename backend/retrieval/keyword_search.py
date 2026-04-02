"""
Keyword search over chunks using SQLite FTS5.

FTS5 provides BM25-style ranking via the built-in `rank` column (lower is
better — it's a negative score). We negate it so higher = more relevant.

match_positions: character offsets of query terms within each chunk's text,
computed server-side so the frontend can highlight without any regex work.
"""
from __future__ import annotations

import json
import re

from sqlalchemy import text
from sqlalchemy.orm import Session


def _build_fts_query(q: str) -> str:
    """
    Sanitize a user query string for FTS5 MATCH syntax.
    Wraps each token in double-quotes to treat them as phrase queries,
    preventing injection via special FTS5 operators.
    """
    tokens = q.strip().split()
    if not tokens:
        return '""'
    return " OR ".join(f'"{t}"' for t in tokens)


def _compute_match_positions(text_content: str, q: str) -> list[tuple[int, int]]:
    """Return (start, end) char offsets of every query-term occurrence in text_content."""
    tokens = [re.escape(t) for t in q.strip().split() if t]
    if not tokens:
        return []
    pattern = "|".join(tokens)
    return [(m.start(), m.end()) for m in re.finditer(pattern, text_content, re.IGNORECASE)]


def search(
    db: Session,
    q: str,
    user_id: str,
    artifact_ids: list[str] | None = None,
    limit: int = 10,
) -> list[dict]:
    """
    Run FTS5 keyword search scoped to a user's artifacts.

    Returns a list of dicts, each containing:
      chunk_row   — raw Chunk ORM row data as dict
      artifact_row — raw Artifact ORM row data as dict
      score       — relevance score (higher = better)
      match_positions — list of (start, end) char offsets in chunk.text
    """
    fts_query = _build_fts_query(q)

    # Build artifact_id filter clause
    artifact_filter_sql = ""
    artifact_params: dict = {}
    if artifact_ids:
        placeholders = ", ".join(f":aid_{i}" for i in range(len(artifact_ids)))
        artifact_filter_sql = f"AND c.artifact_id IN ({placeholders})"
        artifact_params = {f"aid_{i}": aid for i, aid in enumerate(artifact_ids)}

    sql = text(f"""
        SELECT
            c.id            AS chunk_id,
            c.artifact_id,
            c.chunk_index,
            c.text          AS chunk_text,
            c.chunk_type,
            c.provenance,
            c.token_count,
            a.id            AS artifact_id,
            a.user_id,
            a.filename,
            a.file_type,
            a.size_bytes,
            a.file_hash,
            a.version_number,
            a.parent_id,
            a.uploaded_by,
            a.upload_timestamp,
            a.first_seen,
            a.last_seen,
            a.extracted_metadata,
            a.embedding_status,
            -chunks_fts.rank AS score
        FROM chunks_fts
        JOIN chunks  c ON chunks_fts.rowid = c.rowid
        JOIN artifacts a ON c.artifact_id = a.id
        WHERE chunks_fts MATCH :fts_query
          AND a.user_id = :user_id
          {artifact_filter_sql}
        ORDER BY chunks_fts.rank
        LIMIT :limit
    """)

    params = {"fts_query": fts_query, "user_id": user_id, "limit": limit, **artifact_params}
    rows = db.execute(sql, params).mappings().all()

    results = []
    for row in rows:
        row = dict(row)
        match_positions = _compute_match_positions(row["chunk_text"], q)
        results.append({**row, "match_positions": match_positions})

    return results
