# ============================================================
# DocuMind — Hugging Face Spaces Docker Build
# ============================================================
# Multi-stage build:
#   Stage 1 (frontend-builder): Builds the Next.js static export.
#   Stage 2 (runtime):          Python 3.11 + FastAPI serving both
#                                the API and the static frontend.
#
# HF Spaces requirement: the app must listen on port 7860.
#
# Environment variables (set as HF Spaces Secrets):
#   GOOGLE_API_KEY   — required: Google AI Studio key for Gemini LLM + embeddings
#
# Optional tuning (set as HF Spaces Variables to reduce free-tier API usage):
#   ENABLE_CONTEXTUAL_ENRICHMENT=false  (saves ~N LLM calls per upload)
#   ENABLE_DOC2QUERY=false              (saves ~3N LLM calls per upload)
#   DOC2QUERY_QUESTIONS=1               (reduce from default 3)
#   ENABLE_QUERY_REWRITING=false        (saves 1 LLM call per chat message)
# ============================================================

# ============================================================
# Stage 1: Build Next.js static export
# ============================================================
FROM node:20-slim AS frontend-builder

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

# NEXT_PUBLIC_API_URL="" means all fetch() calls use relative paths.
# FastAPI serves both the static files and the API from the same port (7860),
# so /upload, /conversations, etc. resolve correctly with no CORS issues.
ENV NEXT_PUBLIC_API_URL=""

RUN npm run build
# Output at /frontend/out/

# ============================================================
# Stage 2: Python runtime — FastAPI + static files
# ============================================================
FROM python:3.11-slim

WORKDIR /app

# System dependencies required by docling (PDF parsing) and ChromaDB
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    libgl1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer cached unless requirements change)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ ./

# Copy the Next.js static export from Stage 1
COPY --from=frontend-builder /frontend/out ./static_frontend

# Create data directories (SQLite + uploads + ChromaDB)
# Note: on HF Spaces free tier, /data is ephemeral and resets on container restart.
RUN mkdir -p /data/uploads /data/chroma

# Runtime environment — all can be overridden via HF Spaces Secrets/Variables
ENV DATABASE_URL="sqlite:////data/db.sqlite3"
ENV UPLOAD_DIR="/data/uploads"
ENV CHROMA_DIR="/data/chroma"
ENV ENABLE_EMBEDDINGS="true"
ENV ENABLE_CONTEXTUAL_ENRICHMENT="true"
ENV ENABLE_DOC2QUERY="true"
ENV ENABLE_QUERY_REWRITING="true"
# GOOGLE_API_KEY is injected at runtime via HF Spaces Secrets — never hardcode here

# HF Spaces requires port 7860
EXPOSE 7860

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
