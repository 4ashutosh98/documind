"""
DocuMind API — application entry point.

Creates the FastAPI app, registers middleware and routers, runs DB init on startup.

Routers registered:
  upload_router    — POST /upload
  artifacts_router — GET|DELETE /artifacts, POST /artifacts/{id}/reembed
  query_router     — POST /query
  chat_router      — POST|GET|DELETE /conversations/*
  dev_router       — POST /dev/reset  (dev/demo helper)

Inactivity cleanup:
  A background asyncio task watches _last_activity. If no user-initiated API
  call has been made for INACTIVITY_TIMEOUT_MINUTES (default 30), all data is
  wiped automatically (same as POST /dev/reset). The timer only starts after
  the first real interaction — the server booting up does not count.
  Set INACTIVITY_TIMEOUT_MINUTES=0 to disable.
"""
import asyncio
import logging
import os
import time
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from database import SessionLocal, init_db
from api.upload import router as upload_router
from api.artifacts import router as artifacts_router
from api.query import router as query_router
from api.chat import router as chat_router
from api.dev import router as dev_router, perform_reset

_log = logging.getLogger(__name__)

_DEFAULT_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "https://ashutoshchoudhari-documind.hf.space",
    "https://huggingface.co",
]


def _parse_allowed_origins() -> list[str]:
    """Return the configured browser origins allowed to call the API."""
    raw = os.environ.get("ALLOWED_ORIGINS", "")
    parsed = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return parsed or list(_DEFAULT_ALLOWED_ORIGINS)


_allowed_origins = _parse_allowed_origins()


def _is_static_request(path: str) -> bool:
    return path.startswith("/_next") or path.endswith(
        (".js", ".css", ".ico", ".png", ".svg", ".woff", ".woff2", ".map")
    )

# ---------------------------------------------------------------------------
# App instance
# ---------------------------------------------------------------------------
app = FastAPI(title="DocuMind API", version="1.0.0")

# CORS: configurable via ALLOWED_ORIGINS env var (comma-separated).
# Defaults cover local dev plus both HF browser origins used by this Space.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register all API routers — each module owns its own prefix and tag.
app.include_router(upload_router)
app.include_router(artifacts_router)
app.include_router(query_router)
app.include_router(chat_router)
app.include_router(dev_router)


# ---------------------------------------------------------------------------
# Inactivity tracking
# ---------------------------------------------------------------------------

# None until the first real user action (server startup itself is not activity).
_last_activity: Optional[float] = None

# Paths that are NOT counted as user activity:
#   /health            — Docker/HF healthcheck probe
#   /artifacts/stream  — SSE endpoint that auto-reconnects every second
#   /_next/*           — Next.js static asset fetches
_INACTIVE_PATHS = {"/health", "/artifacts/stream"}
_INACTIVE_PREFIXES = ("/_next",)


@app.middleware("http")
async def track_activity(request: Request, call_next):
    """Update _last_activity on every meaningful user-initiated request."""
    global _last_activity
    path = request.url.path
    origin = request.headers.get("origin", "-")
    should_log = not _is_static_request(path)
    if should_log:
        _log.info("[http] -> %s %s origin=%s", request.method, path, origin)
    is_inactive = (
        path in _INACTIVE_PATHS
        or any(path.startswith(p) for p in _INACTIVE_PREFIXES)
        # Static file extensions served by the catch-all frontend route
        or _is_static_request(path)
    )
    if not is_inactive:
        _last_activity = time.time()
    try:
        response = await call_next(request)
    except Exception:
        if should_log:
            _log.exception("[http] !! %s %s origin=%s", request.method, path, origin)
        raise
    if should_log:
        _log.info("[http] <- %s %s status=%s", request.method, path, response.status_code)
    return response


async def _inactivity_cleanup_loop() -> None:
    """
    Background task: wipe all data when the app has been idle too long.

    Runs forever, waking every 60 seconds to check. Only fires after the first
    real user interaction (_last_activity is not None). After wiping, resets
    _last_activity to None so the timer only restarts on the next interaction.
    """
    global _last_activity
    timeout = settings.inactivity_timeout_minutes * 60

    while True:
        await asyncio.sleep(60)

        if timeout <= 0 or _last_activity is None:
            continue  # cleanup disabled or no activity yet

        idle_seconds = time.time() - _last_activity
        if idle_seconds < timeout:
            continue

        _log.info(
            "Inactivity timeout reached (idle %.0fs / limit %ds) — wiping all data.",
            idle_seconds,
            timeout,
        )
        try:
            db = SessionLocal()
            try:
                perform_reset(db)
            finally:
                db.close()
            _log.info("Auto-reset complete.")
        except Exception as exc:
            _log.error("Auto-reset failed: %s", exc)

        # Reset timer — next cleanup only after a fresh interaction
        _last_activity = None


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup() -> None:
    """
    Run once when the server starts.

    Initialises the SQLite schema (tables + FTS5 virtual table + sync triggers)
    and launches the background inactivity cleanup task.
    """
    logging.basicConfig(
        level=logging.INFO if settings.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    _log.info(
        "DocuMind starting — LLM: %s | embed: %s | enrichment: %s | doc2query: %s | verbose: %s",
        settings.groq_model,
        settings.gemini_embed_model,
        settings.enable_contextual_enrichment,
        settings.enable_doc2query,
        settings.verbose,
    )
    _log.info("Allowed browser origins: %s", ", ".join(_allowed_origins))
    init_db()
    asyncio.create_task(_inactivity_cleanup_loop())


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    """Lightweight liveness probe — used by Docker healthcheck."""
    return {"status": "ok"}


@app.get("/api-info")
def api_info() -> dict:
    """Return model config and rate limit info for the API keys modal."""
    return {
        "groq_model": settings.groq_model,
        "groq_configured": bool(settings.groq_api_key),
        "embed_model": settings.gemini_embed_model,
        "embed_configured": bool(settings.google_api_key),
        "features": {
            "contextual_enrichment": settings.enable_contextual_enrichment,
            "doc2query": settings.enable_doc2query,
            "query_rewriting": settings.enable_query_rewriting,
            "embeddings": settings.enable_embeddings,
        },
        "rate_limits": {
            "groq_free_requests_per_day": 14400,
            "groq_free_requests_per_minute": 30,
            "gemini_embed_requests_per_day": 1500,
        },
    }


# ---------------------------------------------------------------------------
# Static file serving (Next.js static export)
#
# When the root Dockerfile builds the Next.js app and copies the output to
# ./static_frontend, FastAPI serves the static assets and HTML pages here.
# The catch-all route at the bottom enables client-side routing (SPA mode).
#
# In local backend-only development the static_frontend directory won't exist,
# so this block is skipped gracefully and the API still works normally.
# ---------------------------------------------------------------------------
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static_frontend")
if os.path.isdir(_STATIC_DIR):
    _next_dir = os.path.join(_STATIC_DIR, "_next")
    if os.path.isdir(_next_dir):
        app.mount("/_next", StaticFiles(directory=_next_dir), name="next-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str):
        # Exact file match (e.g. favicon.ico, robots.txt)
        candidate = os.path.join(_STATIC_DIR, full_path)
        if os.path.isfile(candidate):
            return FileResponse(candidate)
        # Next.js trailingSlash pages: /chat → /chat/index.html
        page_html = os.path.join(_STATIC_DIR, full_path, "index.html")
        if os.path.isfile(page_html):
            return FileResponse(page_html)
        # Default: serve root index.html (client-side routing handles the rest)
        return FileResponse(os.path.join(_STATIC_DIR, "index.html"))
