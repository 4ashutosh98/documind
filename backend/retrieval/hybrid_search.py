"""
Hybrid search: RRF (Reciprocal Rank Fusion) of FTS5 + semantic results.

RRF formula: score(d) = Σ 1 / (k + rank_i)  where k=60 (standard constant).
Deduplicates by chunk_id, preserves the richer row from whichever list had it.

Semantic search is only run against artifacts with embedding_status='ready'.
If none are ready yet (background embedding still in progress) we fall back to
pure FTS5 so the user always gets a response immediately after upload.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from config import settings
from models.artifact import Artifact
from retrieval.keyword_search import search as fts_search
from retrieval.query_transformer import rewrite_query
from retrieval.semantic_search import search as sem_search

_RRF_K = 60


def _rrf_merge(
    lists: list[list[dict]],
    limit: int,
) -> list[dict]:
    """
    Merge ranked lists with RRF. Each list item must have 'chunk_id'.
    Assumes lists[0] = FTS results, lists[1] = semantic results.
    Tags each merged row with search_type: "keyword", "semantic", or "hybrid".
    """
    scores: dict[str, float] = {}
    rows: dict[str, dict] = {}
    appearances: dict[str, set[int]] = {}  # chunk_id → set of list indices

    for list_idx, ranked in enumerate(lists):
        for rank, row in enumerate(ranked, start=1):
            cid = row["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (_RRF_K + rank)
            if cid not in rows:
                rows[cid] = row
            appearances.setdefault(cid, set()).add(list_idx)

    merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
    result = []
    for cid, rrf_score in merged:
        row = dict(rows[cid])
        row["score"] = round(rrf_score, 6)
        app = appearances[cid]
        if len(app) > 1:
            row["search_type"] = "hybrid"
        elif 0 in app:
            row["search_type"] = "keyword"
        else:
            row["search_type"] = "semantic"
        result.append(row)
    return result


def _ready_artifact_ids(
    db: Session,
    user_id: str,
    artifact_ids: list[str] | None,
) -> list[str]:
    """
    Return artifact IDs that have embedding_status='ready' for this user.
    If artifact_ids is provided, filter to that subset.
    Returns [] if no embeddings are ready yet.
    """
    q = db.query(Artifact.id).filter(
        Artifact.user_id == user_id,
        Artifact.embedding_status == "ready",
    )
    if artifact_ids:
        q = q.filter(Artifact.id.in_(artifact_ids))
    return [row.id for row in q.all()]


def search(
    db: Session,
    q: str,
    user_id: str,
    artifact_ids: list[str] | None = None,
    limit: int = 10,
) -> list[dict]:
    """
    Hybrid retrieval:
      1. Optionally rewrite query via LangChain LCEL
      2. FTS5 keyword search (always)
      3. Semantic search (only for artifacts with embedding_status='ready')
      4. RRF merge — falls back to FTS5 if no embeddings are ready
    """
    # FTS5 uses the ORIGINAL query: keyword matching works best with exact user terms.
    # Rewriting "professor" → "faculty advisor" breaks BM25 exact-match — don't do it.
    fts_results = fts_search(db, q, user_id, artifact_ids, limit=limit)

    if not settings.enable_embeddings:
        for r in fts_results:
            r["search_type"] = "keyword"
        return fts_results

    # Only search ChromaDB for artifacts whose embedding is complete
    ready_ids = _ready_artifact_ids(db, user_id, artifact_ids)
    if not ready_ids:
        for r in fts_results:
            r["search_type"] = "keyword"
        return fts_results  # Background embedding not done yet — fall back to FTS5

    # Semantic search uses the REWRITTEN query: embedding benefits from cleaned/expanded intent.
    sem_q = rewrite_query(q)
    sem_results = sem_search(db, sem_q, user_id, ready_ids, limit=limit)

    if not sem_results:
        for r in fts_results:
            r["search_type"] = "keyword"
        return fts_results  # No sufficiently similar chunks or Ollama unreachable

    return _rrf_merge([fts_results, sem_results], limit=limit)
