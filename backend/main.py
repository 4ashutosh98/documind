"""
DocuMind API — application entry point.

Creates the FastAPI app, registers middleware and routers, runs DB init on startup.

Routers registered:
  upload_router    — POST /upload
  artifacts_router — GET|DELETE /artifacts, POST /artifacts/{id}/reembed
  query_router     — POST /query
  chat_router      — POST|GET|DELETE /conversations/*
  dev_router       — POST /dev/reset  (dev/demo helper)
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from database import init_db
from api.upload import router as upload_router
from api.artifacts import router as artifacts_router
from api.query import router as query_router
from api.chat import router as chat_router
from api.dev import router as dev_router

# FastAPI application instance — title and version appear in the auto-generated
# OpenAPI docs at /docs and /redoc.
app = FastAPI(title="DocuMind API", version="1.0.0")

# CORS: configurable via ALLOWED_ORIGINS env var (comma-separated).
# In the HF Spaces deployment, the frontend is served from the same origin so
# CORS is not needed for frontend↔API calls. The default allows local dev.
_allowed_origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
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


@app.on_event("startup")
def on_startup() -> None:
    """
    Run once when the server starts.

    Creates all SQLAlchemy tables (via metadata.create_all), the FTS5 virtual
    table, and the three sync triggers that keep the FTS index consistent with
    the chunks table.  Safe to call multiple times — every DDL statement uses
    IF NOT EXISTS so existing schemas are not modified.
    """
    init_db()


@app.get("/health")
def health() -> dict:
    """
    Lightweight liveness probe.

    Used by the Docker healthcheck (``curl -f /health``) and by any
    load balancer / orchestrator that needs to confirm the process is up.

    Returns:
        {"status": "ok"}
    """
    return {"status": "ok"}


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
