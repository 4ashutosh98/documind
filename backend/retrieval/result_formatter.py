"""
Assembles a QueryResponse Pydantic model from raw search result dicts.

Both keyword_search and semantic_search return a list of flat dicts with
columns from a JOIN of chunks + artifacts.  This module converts that raw
format into the typed response schema the API returns to the frontend.

The same formatter is shared by:
  - POST /query          (direct query endpoint)
  - POST /conversations/{id}/messages  (chat endpoint)
  - GET  /conversations/{id}/messages  (history re-hydration)
"""
from __future__ import annotations

import json
from datetime import datetime

from models.schemas import (
    ArtifactSummary,
    ChunkResponse,
    ProvenanceRef,
    QueryMatch,
    QueryResponse,
)


def _parse_dt(val) -> datetime:
    """
    Coerce a value to datetime.

    SQLAlchemy may return either a datetime object or an ISO-format string
    depending on the driver and whether the row came from a raw SQL result or
    an ORM query.

    Args:
        val: A datetime instance or an ISO-format datetime string.

    Returns:
        datetime instance.
    """
    if isinstance(val, datetime):
        return val
    return datetime.fromisoformat(str(val))


def format_results(q: str, raw_results: list[dict]) -> QueryResponse:
    """
    Convert raw search result dicts into a typed QueryResponse.

    Args:
        q:           The original query string (echoed in the response for display).
        raw_results: List of flat dicts from keyword_search or hybrid_search,
                     each containing columns from chunks JOIN artifacts, plus:
                       - score          — relevance score (higher = better)
                       - match_positions — list of (start, end) char offsets
                       - search_type    — "keyword" | "semantic" | "hybrid" | None

    Returns:
        QueryResponse with:
          - query: the original query
          - total: number of results
          - results: list of QueryMatch objects

    Each QueryMatch contains:
      - chunk:    ChunkResponse (id, text, provenance, etc.)
      - artifact: ArtifactSummary (filename, file_type, metadata, etc.)
      - score:    relevance score
      - match_positions: [(start, end), …] for client-side highlighting
      - search_type: which retrieval path produced this result
    """
    matches: list[QueryMatch] = []

    for row in raw_results:
        # Parse provenance JSON → dict for ProvenanceRef constructor
        provenance_dict = {}
        try:
            provenance_dict = json.loads(row.get("provenance") or "{}")
        except (json.JSONDecodeError, TypeError):
            pass

        chunk = ChunkResponse(
            id=row["chunk_id"],
            artifact_id=row["artifact_id"],
            chunk_index=row["chunk_index"],
            text=row["chunk_text"],
            chunk_type=row["chunk_type"],
            provenance=ProvenanceRef(**provenance_dict),
            token_count=row.get("token_count"),
        )

        # Parse extracted_metadata JSON → dict for ArtifactSummary
        extracted = {}
        try:
            extracted = json.loads(row.get("extracted_metadata") or "{}")
        except (json.JSONDecodeError, TypeError):
            pass

        artifact = ArtifactSummary(
            id=row["artifact_id"],
            user_id=row["user_id"],
            filename=row["filename"],
            file_type=row["file_type"],
            size_bytes=row["size_bytes"],
            file_hash=row["file_hash"],
            version_number=row["version_number"],
            parent_id=row.get("parent_id"),
            uploaded_by=row["uploaded_by"],
            upload_timestamp=_parse_dt(row["upload_timestamp"]),
            first_seen=_parse_dt(row["first_seen"]),
            last_seen=_parse_dt(row["last_seen"]),
            extracted_metadata=extracted,
            embedding_status=row.get("embedding_status", "none"),
        )

        matches.append(QueryMatch(
            chunk=chunk,
            artifact=artifact,
            score=row.get("score"),
            match_positions=row.get("match_positions", []),
            # search_type is set by hybrid_search._rrf_merge or the FTS fallback paths
            search_type=row.get("search_type"),
        ))

    return QueryResponse(query=q, total=len(matches), results=matches)
