"""
POST /upload

Full upload pipeline:
  1. Hash bytes in memory (SHA-256, before any disk write)
  2. Exact duplicate check (same user + same hash) → return early
  3. Version detection (same user + same filename, different hash)
  4. Write blob to disk only if not already present
  5. Metadata reuse: if any user already parsed this hash, copy their extracted_metadata
  6. Parse fresh via Docling (PDF/DOCX) or pandas (XLSX) if not reusable
  7. Chunk the ParseResult
  8. Optional: enrich chunks with Ollama contextual enrichment (sync, before response)
  9. Persist artifact record + chunks → response returned here (FTS5 available immediately)
 10. Background: embed chunks into ChromaDB, update embedding_status → "ready"
     (hybrid search becomes available once this completes)
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, UploadFile

_log = logging.getLogger(__name__)
from sqlalchemy.orm import Session

from chunking.base import ChunkRecord
from chunking.dispatcher import chunk_result
from chunking.enricher import enrich_chunks
from config import settings
from database import SessionLocal, get_db
from ingestion.base import ParseResult
from ingestion.dispatcher import parse_file, SUPPORTED_EXTENSIONS
from ingestion.embedder import embed_and_index
from models.artifact import Artifact
from models.schemas import UploadResponse
from storage import artifact_store, chunk_store

router = APIRouter()


# ---------------------------------------------------------------------------
# Background embedding task
# ---------------------------------------------------------------------------

def _embed_in_background(
    artifact_id: str,
    chunk_records: list[ChunkRecord],
    chunk_ids: list[str],
    user_id: str,
    filename: str,
    source_chunk_ids: list[str] | None = None,
    groq_api_key: str = "",
    google_api_key: str = "",
) -> None:
    """
    Run in FastAPI BackgroundTasks (thread pool).
    Sets embedding_status = 'pending', runs embed_and_index, then sets 'ready'.
    Uses its own DB session since the request session has already closed.
    groq_api_key: captured from request at upload time, used for doc2query generation.
    """
    db = SessionLocal()
    try:
        artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
        if artifact:
            artifact.embedding_status = "pending"
            db.commit()

        embedded = 0

        # Fast path: copy vectors from source user's chunks (no Ollama calls)
        if source_chunk_ids and settings.enable_embeddings:
            from storage.vector_store import copy_chunk_embeddings
            embedded = copy_chunk_embeddings(
                source_chunk_ids=source_chunk_ids,
                target_chunk_ids=chunk_ids,
                target_artifact_id=artifact_id,
                target_user_id=user_id,
                target_filename=filename,
            )

        # Slow path: source not indexed yet (or no source) — embed fresh
        if embedded == 0:
            figures_b64 = None
            if artifact and settings.enable_image_description:
                try:
                    meta = json.loads(artifact.extracted_metadata or "{}")
                    raw = meta.get("figures_b64")
                    figures_b64 = raw if isinstance(raw, list) else None
                except Exception:
                    pass
            embedded = embed_and_index(
                chunks=chunk_records,
                chunk_ids=chunk_ids,
                artifact_id=artifact_id,
                user_id=user_id,
                filename=filename,
                figures_b64=figures_b64,
                groq_api_key=groq_api_key,
                google_api_key=google_api_key,
            )

        artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
        if artifact:
            # Only mark "ready" if at least one chunk was actually embedded.
            # If Ollama was unreachable (embedded == 0), stay "pending" so the
            # frontend keeps showing the yellow triangle — honest, retryable state.
            if embedded > 0:
                artifact.embedding_status = "ready"
                # Record which features were active during this indexing run so
                # the UI can show accurate per-artifact feature pills.
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
            db.commit()
    except Exception as e:
        _log.error("Embedding background task failed for artifact %s: %s", artifact_id, e)
        # Reset to "none" so the user can trigger a retry by re-uploading.
        try:
            artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
            if artifact:
                artifact.embedding_status = "none"
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/upload", response_model=UploadResponse, status_code=201)
async def upload_file(
    file: UploadFile = File(...),
    user_id: str = Form(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
    x_groq_api_key: str = Header(default="", alias="X-Groq-Api-Key"),
    x_google_api_key: str = Header(default="", alias="X-Google-Api-Key"),
):
    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type {suffix!r}. Supported: {sorted(SUPPORTED_EXTENSIONS)}",
        )

    groq_key = x_groq_api_key or settings.groq_api_key
    google_key = x_google_api_key or settings.google_api_key

    # 1. Read + hash in memory
    content = await file.read()
    file_hash = hashlib.sha256(content).hexdigest()
    size_bytes = len(content)
    _log.info("[upload] received %s (%.1f KB) from %s (user key: %s)",
              file.filename, size_bytes / 1024, user_id, "yes" if x_groq_api_key else "no")

    # 2. Exact duplicate for this user
    existing = artifact_store.check_duplicate(db, user_id, file_hash)
    if existing:
        _log.info("[upload] duplicate detected for %s (artifact %s)", file.filename, existing.id)
        artifact_store.touch_last_seen(db, existing)
        # If this artifact was never embedded (status "none") and embeddings are
        # now enabled, kick off the background task so the user sees progress icons.
        if settings.enable_embeddings and existing.embedding_status == "none":
            existing_chunks = chunk_store.get_by_artifact(db, existing.id)
            if existing_chunks:
                chunk_records_for_bg = [
                    ChunkRecord(
                        text=c.text,
                        chunk_index=c.chunk_index,
                        chunk_type=c.chunk_type,
                        provenance=json.loads(c.provenance or "{}"),
                        token_count=c.token_count,
                    )
                    for c in existing_chunks
                ]
                existing.embedding_status = "pending"
                db.commit()
                background_tasks.add_task(
                    _embed_in_background,
                    artifact_id=existing.id,
                    chunk_records=chunk_records_for_bg,
                    chunk_ids=[c.id for c in existing_chunks],
                    user_id=user_id,
                    filename=existing.filename,
                    groq_api_key=groq_key,
                    google_api_key=google_key,
                )
        return UploadResponse(
            artifact_id=existing.id,
            status="duplicate",
            version_number=existing.version_number,
            message="File already exists. Returning existing artifact.",
        )

    # 3. Version detection
    parent_id: str | None = None
    version_number = 1
    prev = artifact_store.get_latest_version_by_filename(db, user_id, file.filename)
    if prev:
        parent_id = prev.id
        version_number = prev.version_number + 1
        _log.info("[upload] new version v%d for %s", version_number, file.filename)
    else:
        _log.info("[upload] new file: %s", file.filename)

    # 4. Write blob (skip if already stored by another user)
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    blob_path = upload_dir / f"{file_hash}{suffix}"
    if not blob_path.exists():
        blob_path.write_bytes(content)

    # 5. Metadata + chunk reuse: another user already parsed this hash?
    existing_metadata = artifact_store.find_existing_metadata(db, file_hash)

    source_chunk_ids: list[str] | None = None
    if existing_metadata is not None:
        parse_result = ParseResult(
            filename=file.filename,
            file_type=suffix.lstrip("."),
            size_bytes=size_bytes,
            file_hash=file_hash,
            extracted_metadata=existing_metadata,
        )
        source_artifact = db.query(Artifact).filter(Artifact.file_hash == file_hash).first()
        existing_chunks = chunk_store.get_by_artifact(db, source_artifact.id) if source_artifact else []
        # Capture source chunk IDs so the background task can copy vectors instead of re-embedding
        if existing_chunks:
            source_chunk_ids = [c.id for c in existing_chunks]
    else:
        # 6. Fresh parse
        parse_result = parse_file(blob_path, file_hash, size_bytes)
        parse_result.filename = file.filename
        existing_chunks = None

    # 7. Create artifact record
    artifact = artifact_store.create_artifact(
        db=db,
        user_id=user_id,
        parse_result=parse_result,
        version_number=version_number,
        parent_id=parent_id,
    )

    # 8. Build chunks (reuse or fresh)
    if existing_chunks:
        chunk_records = [
            ChunkRecord(
                text=c.text,
                chunk_index=c.chunk_index,
                chunk_type=c.chunk_type,
                provenance=json.loads(c.provenance or "{}"),
                token_count=c.token_count,
            )
            for c in existing_chunks
        ]
    else:
        chunk_records = chunk_result(parse_result)

    _log.info("[upload] chunked into %d chunks", len(chunk_records))

    # Contextual enrichment via Groq (sync, before response — no-op if disabled or unreachable)
    doc_start = (parse_result.markdown_content or "")[:600]
    chunk_records = await enrich_chunks(
        chunks=chunk_records,
        filename=file.filename,
        file_type=parse_result.file_type,
        doc_start=doc_start,
        groq_api_key=groq_key,
    )

    # 9. Persist chunks → FTS5 index updated, keyword search available immediately
    persisted = chunk_store.bulk_insert(db, artifact.id, chunk_records)
    _log.info("[upload] persisted %d chunks to SQLite/FTS5 — keyword search available", len(persisted))

    # 10. Schedule vector embedding as background task (non-blocking)
    #     Set "pending" synchronously so the next listArtifacts call sees it immediately
    #     and the frontend polling loop starts before the background task runs.
    #     embedding_status: "none" → "pending" → "ready" (hybrid search available on ready)
    if settings.enable_embeddings:
        artifact.embedding_status = "pending"
        db.commit()
        _log.info("[upload] scheduling background embedding for artifact %s", artifact.id)
        background_tasks.add_task(
            _embed_in_background,
            artifact_id=artifact.id,
            chunk_records=chunk_records,
            chunk_ids=[c.id for c in persisted],
            user_id=user_id,
            filename=file.filename,
            source_chunk_ids=source_chunk_ids,
            groq_api_key=groq_key,
            google_api_key=google_key,
        )

    status = "new_version" if parent_id else "created"
    message = (
        f"New version {version_number} created."
        if parent_id
        else "File ingested successfully."
    )

    return UploadResponse(
        artifact_id=artifact.id,
        status=status,
        version_number=version_number,
        message=message,
    )
