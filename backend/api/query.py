"""
POST /query — direct query endpoint with hybrid search and source highlighting.

Accepts a natural-language or keyword query and returns matched chunks from
the user's artifacts with:
  - FTS5 BM25 keyword matching (always)
  - Semantic search via ChromaDB cosine similarity (when embeddings are ready)
  - RRF fusion of both result sets (when both are available)
  - match_positions: char offsets for client-side text highlighting
  - search_type: "keyword" | "semantic" | "hybrid" per result

Gracefully degrades to FTS5-only when:
  - enable_embeddings is False (config)
  - No artifacts have embedding_status = "ready"
  - Ollama is unreachable at query time

This endpoint is the programmatic API counterpart to the chat interface.
The chat interface (POST /conversations/{id}/messages) uses the same hybrid
search under the hood but also runs an Ollama RAG chain to generate a natural
language answer.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models.schemas import QueryRequest, QueryResponse
from retrieval.hybrid_search import search
from retrieval.result_formatter import format_results

router = APIRouter()


@router.post("/query", response_model=QueryResponse)
def query_artifacts(request: QueryRequest, db: Session = Depends(get_db)) -> QueryResponse:
    """
    Run a hybrid search over the user's document chunks.

    The search pipeline:
      1. FTS5 keyword search (always runs; zero-dependency baseline)
      2. If any artifacts have embedding_status='ready':
           a. Optionally rewrite the query via Ollama (enable_query_rewriting)
           b. Embed the query via nomic-embed-text → ChromaDB cosine search
           c. Also query the Doc2Query hypothetical questions collection
           d. RRF-merge FTS + semantic results
      3. Falls back to FTS5-only if Ollama unreachable or no embeddings ready

    Args:
        request: QueryRequest containing:
                   q            — query string (non-empty)
                   user_id      — scopes search to this user's artifacts
                   artifact_ids — optional list to restrict search scope
                   limit        — max results (default 10)
        db:      SQLAlchemy session (injected by FastAPI).

    Returns:
        QueryResponse with matched chunks, scores, match_positions, and
        search_type badges per result.

    Raises:
        422: if the query string is empty or whitespace-only.
    """
    if not request.q.strip():
        raise HTTPException(status_code=422, detail="Query string cannot be empty")

    # Run hybrid search (FTS5 + semantic, or FTS5-only fallback)
    raw = search(
        db=db,
        q=request.q,
        user_id=request.user_id,
        artifact_ids=request.artifact_ids,
        limit=request.limit,
    )

    # Convert flat row dicts → typed Pydantic response
    return format_results(request.q, raw)
