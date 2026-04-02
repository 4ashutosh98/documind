"""
Artifact management endpoints.

GET    /artifacts?user_id=             — list all artifacts for a user
GET    /artifacts/stream?user_id=      — SSE stream: pushes status updates while any artifact is pending
GET    /artifacts/{id}                 — full artifact detail with chunks
DELETE /artifacts/{id}?user_id=        — delete with blob GC + version chain orphaning
POST   /artifacts/{id}/reembed?user_id= — re-trigger embedding pipeline in background

Design notes
------------
_serialize_artifact / _serialize_chunk:
    Raw SQLAlchemy ORM rows are converted to Pydantic response models in these
    helpers rather than inline in each endpoint.  This keeps the endpoint
    handlers thin and the serialisation logic testable in isolation.

Blob GC on delete:
    delete_artifact calls artifact_store.delete_artifact which reference-counts
    the blob (how many artifacts share this hash) and deletes the physical file
    only when the count reaches 0.  It also removes the artifact's vectors from
    ChromaDB via delete_artifact_vectors.

Re-embed endpoint:
    The /reembed endpoint exists so users can:
      - Re-index a document after upgrading Ollama models
      - Force re-generation of Doc2Query hypothetical questions
      - Recover from a partial or failed initial embedding run
    It sets embedding_status to "pending" immediately (visible in the UI) then
    runs the full embed_and_index pipeline in a background thread.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from chunking.base import ChunkRecord
from database import SessionLocal, get_db
from ingestion.embedder import embed_and_index
from models.artifact import Artifact
from models.schemas import ArtifactDetail, ArtifactSummary, ChunkResponse, DeleteResponse, ProvenanceRef
from storage import artifact_store, chunk_store
from storage.vector_store import delete_artifact_vectors

router = APIRouter()
_log = logging.getLogger(__name__)


def _serialize_artifact(artifact) -> ArtifactSummary:
    """
    Convert an Artifact ORM row to an ArtifactSummary Pydantic model.

    Handles the JSON-encoded extracted_metadata column — deserialises it to a
    dict for the response schema.  Returns an empty dict on JSON parse failure
    rather than crashing.

    Args:
        artifact: Artifact ORM instance from SQLAlchemy.

    Returns:
        ArtifactSummary Pydantic model ready for serialisation.
    """
    extracted = {}
    try:
        extracted = json.loads(artifact.extracted_metadata or "{}")
    except (json.JSONDecodeError, TypeError):
        pass
    return ArtifactSummary(
        id=artifact.id,
        user_id=artifact.user_id,
        filename=artifact.filename,
        file_type=artifact.file_type,
        size_bytes=artifact.size_bytes,
        file_hash=artifact.file_hash,
        version_number=artifact.version_number,
        parent_id=artifact.parent_id,
        uploaded_by=artifact.uploaded_by,
        upload_timestamp=artifact.upload_timestamp,
        first_seen=artifact.first_seen,
        last_seen=artifact.last_seen,
        extracted_metadata=extracted,
        embedding_status=artifact.embedding_status,
    )


def _serialize_chunk(chunk) -> ChunkResponse:
    """
    Convert a Chunk ORM row to a ChunkResponse Pydantic model.

    Deserialises the JSON-encoded provenance column to a ProvenanceRef model.
    Returns an empty ProvenanceRef on parse failure.

    Args:
        chunk: Chunk ORM instance from SQLAlchemy.

    Returns:
        ChunkResponse Pydantic model ready for serialisation.
    """
    provenance = {}
    try:
        provenance = json.loads(chunk.provenance or "{}")
    except (json.JSONDecodeError, TypeError):
        pass
    return ChunkResponse(
        id=chunk.id,
        artifact_id=chunk.artifact_id,
        chunk_index=chunk.chunk_index,
        text=chunk.text,
        chunk_type=chunk.chunk_type,
        provenance=ProvenanceRef(**provenance),
        token_count=chunk.token_count,
    )


@router.get("/artifacts", response_model=list[ArtifactSummary])
def list_artifacts(user_id: str = Query(...), db: Session = Depends(get_db)) -> list[ArtifactSummary]:
    """
    Return all artifacts owned by a user, newest first.

    Used by the sidebar file list in the frontend on initial load and after
    uploads.  During background indexing the frontend switches to the SSE
    stream (/artifacts/stream) instead of polling this endpoint.

    Args:
        user_id: Query parameter — the user whose artifacts to list.
        db:      SQLAlchemy session (injected).

    Returns:
        List of ArtifactSummary objects ordered by upload_timestamp descending.
    """
    artifacts = artifact_store.list_artifacts(db, user_id)
    return [_serialize_artifact(a) for a in artifacts]


@router.get("/artifacts/stream")
async def stream_artifact_status(user_id: str = Query(...)) -> StreamingResponse:
    """
    SSE stream that pushes artifact list updates while any artifact is indexing.

    Replaces the frontend's 5-second polling loop.  The client opens this
    connection once (when a pending artifact is detected) and receives updates
    as the background embedding task progresses.  The stream closes automatically
    once all artifacts have left the 'pending' state.

    Protocol:
      - Each event is a JSON array of ArtifactSummary objects (same schema as
        GET /artifacts).  The frontend can replace its local state directly.
      - A final ``event: done`` signals that no more updates will come and the
        client should close the EventSource.

    Uses its own SessionLocal (not FastAPI's get_db) because the generator
    outlives the request's dependency lifecycle.  db.expire_all() is called
    before each query so SQLAlchemy re-reads committed rows from the background
    embedding task rather than returning stale cached values.

    Args:
        user_id: Query parameter — which user's artifacts to watch.

    Returns:
        StreamingResponse with Content-Type: text/event-stream.
    """
    async def _generate():
        db = SessionLocal()
        try:
            prev_statuses: dict[str, str] = {}
            while True:
                # expire_all forces SQLAlchemy to re-fetch on next access,
                # picking up commits from the background embedding thread
                db.expire_all()
                artifacts = artifact_store.list_artifacts(db, user_id)
                cur_statuses = {a.id: a.embedding_status for a in artifacts}

                if cur_statuses != prev_statuses:
                    prev_statuses = cur_statuses.copy()
                    payload = [_serialize_artifact(a).model_dump(mode="json") for a in artifacts]
                    yield f"data: {json.dumps(payload)}\n\n"

                if not any(s == "pending" for s in cur_statuses.values()):
                    yield "event: done\ndata: {}\n\n"
                    break

                await asyncio.sleep(1)
        finally:
            db.close()

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx response buffering
            "Connection": "keep-alive",
        },
    )


@router.get("/artifacts/{artifact_id}", response_model=ArtifactDetail)
def get_artifact(artifact_id: str, db: Session = Depends(get_db)) -> ArtifactDetail:
    """
    Return the full detail for one artifact including all its chunks.

    Used by the artifact detail modal.  Chunks are loaded in chunk_index order
    and include their full text, type, provenance, and token count.

    Args:
        artifact_id: UUID path parameter.
        db:          SQLAlchemy session (injected).

    Returns:
        ArtifactDetail (extends ArtifactSummary with a chunks list).

    Raises:
        404: if no artifact with this ID exists.
    """
    artifact = artifact_store.get_artifact_by_id(db, artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    chunks = chunk_store.get_by_artifact(db, artifact_id)
    summary = _serialize_artifact(artifact)
    # Extend the summary dict with the serialised chunk list
    return ArtifactDetail(**summary.model_dump(), chunks=[_serialize_chunk(c) for c in chunks])


@router.delete("/artifacts/{artifact_id}", response_model=DeleteResponse)
def delete_artifact(
    artifact_id: str,
    user_id: str = Query(...),
    db: Session = Depends(get_db),
) -> DeleteResponse:
    """
    Delete an artifact and its chunks, with blob GC and vector cleanup.

    Steps:
      1. Verify ownership (403 if user_id doesn't match).
      2. artifact_store.delete_artifact: orphan children, reference-count blob,
         delete DB record (cascades to chunks), conditionally delete blob file.
      3. delete_artifact_vectors: remove this artifact's vectors from ChromaDB
         (no-op if the artifact was never embedded).

    Args:
        artifact_id: UUID path parameter.
        user_id:     Query parameter — must match artifact.user_id.
        db:          SQLAlchemy session (injected).

    Returns:
        DeleteResponse with blob_deleted=True if the physical file was removed.

    Raises:
        404: artifact not found.
        403: user_id does not own this artifact.
    """
    artifact = artifact_store.get_artifact_by_id(db, artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if artifact.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this artifact")

    blob_deleted = artifact_store.delete_artifact(db, artifact)
    # Remove vectors from ChromaDB (no-op if never embedded — exception is suppressed inside)
    delete_artifact_vectors(artifact_id)
    return DeleteResponse(
        artifact_id=artifact_id,
        blob_deleted=blob_deleted,
        message="Artifact deleted." + (" Physical file removed." if blob_deleted else ""),
    )


# ---------------------------------------------------------------------------
# Re-embed
# ---------------------------------------------------------------------------

def _reembed_in_background(
    artifact_id: str,
    chunk_records: list[ChunkRecord],
    chunk_ids: list[str],
    user_id: str,
    filename: str,
) -> None:
    """
    Background task: clears stale vectors and re-embeds all chunks.

    Must use its own DB session because the request session is closed by the
    time this runs.  The pattern mirrors _embed_in_background in upload.py.

    Steps:
      1. Delete any stale vectors for this artifact from ChromaDB (idempotent).
      2. Run embed_and_index: embed chunks via nomic-embed-text, optionally
         generate Doc2Query questions, upsert all into ChromaDB.
      3. Set embedding_status = "ready" if at least one chunk was embedded,
         or "pending" if Ollama was unreachable (honest state for the frontend).
      4. Update indexed_features in extracted_metadata so the UI can show
         accurate per-artifact feature pills (Doc2Query ❓, Context ✦, Vision 👁).
      5. On any exception, set embedding_status = "none" and log the error.

    Args:
        artifact_id:   UUID of the artifact to re-embed.
        chunk_records: ChunkRecord objects (text, provenance, type).
        chunk_ids:     Corresponding Chunk.id UUIDs (parallel list).
        user_id:       Owner — used as ChromaDB metadata filter key.
        filename:      Original filename — stored as ChromaDB metadata.
    """
    db = SessionLocal()
    try:
        # Clear stale vectors first so re-embed is fully idempotent
        delete_artifact_vectors(artifact_id)

        embedded = embed_and_index(
            chunks=chunk_records,
            chunk_ids=chunk_ids,
            artifact_id=artifact_id,
            user_id=user_id,
            filename=filename,
        )

        from config import settings
        artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
        if artifact:
            if embedded > 0:
                artifact.embedding_status = "ready"
                # Record which features were active so the UI shows accurate pills
                try:
                    meta = json.loads(artifact.extracted_metadata or "{}")
                    meta["indexed_features"] = {
                        "doc2query": settings.enable_doc2query,
                        "contextual_enrichment": settings.enable_contextual_enrichment,
                        "image_description": settings.enable_image_description,
                    }
                    artifact.extracted_metadata = json.dumps(meta)
                except Exception:
                    pass
            else:
                # Ollama was unreachable — stay "pending" so the user can retry
                artifact.embedding_status = "pending"
            db.commit()
    except Exception as e:
        _log.error("Re-embedding failed for artifact %s: %s", artifact_id, e)
        # Reset to "none" so the user can trigger a retry by clicking Re-index
        try:
            artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
            if artifact:
                artifact.embedding_status = "none"
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


@router.post("/artifacts/{artifact_id}/reembed")
def reembed_artifact(
    artifact_id: str,
    user_id: str = Query(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
) -> dict:
    """
    Re-trigger the full embedding pipeline for an existing artifact.

    Returns immediately; the actual embedding runs in a background thread.
    Sets embedding_status = "pending" synchronously so the frontend shows
    the yellow triangle before the background task starts.

    Use cases:
      - Re-index after pulling a better Ollama embedding model
      - Force Doc2Query question regeneration
      - Recover from a partially failed or interrupted initial embedding

    Args:
        artifact_id:      UUID path parameter.
        user_id:          Query parameter — must own the artifact.
        background_tasks: FastAPI dependency for background task scheduling.
        db:               SQLAlchemy session (injected).

    Returns:
        {"artifact_id": ..., "embedding_status": "pending", "message": ...}

    Raises:
        404: artifact not found.
        403: user_id does not own this artifact.
        422: artifact has no chunks (re-upload required to re-ingest).
    """
    artifact = artifact_store.get_artifact_by_id(db, artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if artifact.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    if artifact.embedding_status == "pending":
        # Already in progress — return the current status without starting another task
        return {"artifact_id": artifact_id, "embedding_status": "pending", "message": "Already in progress"}

    chunks = chunk_store.get_by_artifact(db, artifact_id)
    if not chunks:
        raise HTTPException(
            status_code=422,
            detail="No chunks found — re-upload the file to re-ingest it.",
        )

    # Rebuild ChunkRecord objects from the persisted Chunk rows
    chunk_records = [
        ChunkRecord(
            text=c.text,
            chunk_index=c.chunk_index,
            chunk_type=c.chunk_type,
            provenance=json.loads(c.provenance or "{}"),
            token_count=c.token_count,
        )
        for c in chunks
    ]

    # Set pending synchronously so the frontend sees the yellow triangle immediately
    artifact.embedding_status = "pending"
    db.commit()

    # Schedule the heavy embedding work to run after the response is sent
    background_tasks.add_task(
        _reembed_in_background,
        artifact_id=artifact_id,
        chunk_records=chunk_records,
        chunk_ids=[c.id for c in chunks],
        user_id=user_id,
        filename=artifact.filename,
    )

    return {"artifact_id": artifact_id, "embedding_status": "pending", "message": "Re-embedding started"}
