# DocuMind

A full-stack RAG (Retrieval-Augmented Generation) application for document ingestion and intelligent Q&A. Upload PDFs, DOCX, and XLSX files and ask questions about them — answers are grounded in your documents with source citations.

**Live demo:** [DocuMind on Hugging Face Spaces](#) *(update link after deploying)*

---

## Features

- **Multi-format ingestion** — PDF, DOCX, and XLSX files parsed into structured chunks with provenance (page, section, row range)
- **Hybrid search** — FTS5 keyword search (BM25) fused with semantic vector search (Reciprocal Rank Fusion) for best-of-both retrieval
- **RAG chat** — Multi-turn conversations powered by Gemini 2.0 Flash, grounded in your uploaded documents with source cards
- **Contextual enrichment** — Each chunk enriched with 1-2 sentences of situational context at index time (Anthropic's Contextual Retrieval pattern)
- **Doc2Query** — Hypothetical questions generated per chunk and indexed as additional retrieval vectors (improves semantic recall)
- **Deduplication + versioning** — Content-addressed blob storage; same file stored once regardless of how many users upload it; re-uploads create version chains
- **Multi-user isolation** — Three demo users with separate document namespaces

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 14 (React 18, TypeScript, Tailwind CSS) |
| Backend | FastAPI + SQLAlchemy + SQLite FTS5 |
| Vector store | ChromaDB (HNSW, cosine distance) |
| LLM + embeddings | Google Gemini 2.0 Flash + text-embedding-004 (768-dim) |
| Orchestration | LangChain LCEL |
| Document parsing | Docling (PDF/DOCX → structured markdown) + pandas (XLSX) |
| Deployment | Docker (Hugging Face Spaces, port 7860) |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Browser (Next.js static export)                            │
│  Landing → Login → Chat (conversations | messages | files)  │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP (same origin, port 7860)
┌──────────────────────────▼──────────────────────────────────┐
│  FastAPI (uvicorn, port 7860)                               │
│                                                             │
│  POST /upload          →  parse → chunk → enrich → embed   │
│  POST /conversations/*/messages  →  hybrid search → RAG    │
│  POST /query           →  hybrid search → ranked results   │
│  GET  /artifacts       →  list / detail / SSE status       │
└──────┬────────────────────────────────┬────────────────────┘
       │                                │
┌──────▼──────┐               ┌────────▼───────┐
│  SQLite     │               │  ChromaDB      │
│  FTS5 index │               │  (HNSW cosine) │
│  + metadata │               │  chunks +      │
│  + chunks   │               │  doc2query Qs  │
└─────────────┘               └────────────────┘
```

### Ingestion pipeline

```
Upload → hash bytes (SHA-256) → dedup check → parse (Docling/pandas)
      → chunk (semantic / row-based) → contextual enrichment (Gemini)
      → persist to SQLite + FTS5  ← FTS5 keyword search available here
      → [background] embed chunks + doc2query questions → ChromaDB
                                  ← hybrid search available here
```

### Retrieval pipeline

```
Query → rewrite (Gemini) → FTS5 keyword search (BM25)
                         → semantic search (ChromaDB cosine)
                         → RRF fusion (k=60)
                         → format results with match positions
```

---

## Local Development

### Prerequisites

- Node 20+ and npm
- Python 3.11+
- Docker (for the containerized backend)
- A Google AI Studio API key: [aistudio.google.com](https://aistudio.google.com) (free, no credit card)

### Backend (Docker)

```bash
# Create a .env file in the backend directory
echo "GOOGLE_API_KEY=your_key_here" > backend/.env

docker-compose up --build
# API at http://localhost:8000
# Docs at http://localhost:8000/docs
```

### Backend (without Docker)

```bash
cd backend
pip install -r requirements.txt
GOOGLE_API_KEY=your_key_here uvicorn main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install

# Create .env.local for local dev
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > .env.local

npm run dev    # http://localhost:3000
```

---

## Deployment (Hugging Face Spaces)

1. Create a new Space → type: **Docker**
2. Push this repository (or connect GitHub)
3. In Space Settings → **Repository Secrets**, add:
   - `GOOGLE_API_KEY` — your Google AI Studio key
4. The Space builds automatically using the root [Dockerfile](Dockerfile)

The Dockerfile does two things in a multi-stage build:
1. Builds the Next.js static export (`npm run build` → `out/`)
2. Copies it into the FastAPI container — FastAPI serves both the UI and the API from port 7860

### Reducing free-tier API usage (optional Space Variables)

| Variable | Default | Effect |
|---|---|---|
| `ENABLE_CONTEXTUAL_ENRICHMENT` | `true` | `false` skips chunk enrichment calls |
| `ENABLE_DOC2QUERY` | `true` | `false` skips hypothetical question generation |
| `DOC2QUERY_QUESTIONS` | `3` | Lower to `1` to reduce ingestion API calls |
| `ENABLE_QUERY_REWRITING` | `true` | `false` skips query rewriting call |

> **Note:** Data is ephemeral on HF Spaces free tier — SQLite, uploads, and ChromaDB vectors reset on container restart. This is expected behavior for a demo.

---

## Running Tests

```bash
cd backend
pytest                          # all tests
pytest tests/test_storage.py    # single file
pytest -k "test_dedup"          # single test by name
```

Tests use an isolated per-test SQLite DB and mock all external API calls — fully offline.

---

## API Reference

```
POST   /upload                              Upload a file (PDF, DOCX, XLSX)
GET    /artifacts?user_id=                  List user's artifacts
GET    /artifacts/stream?user_id=           SSE: live embedding status updates
GET    /artifacts/{id}                      Artifact detail + all chunks
DELETE /artifacts/{id}?user_id=             Delete artifact (blob GC if last ref)
POST   /artifacts/{id}/reembed?user_id=     Re-trigger embedding pipeline
POST   /query                               Hybrid search (FTS5 + semantic)
POST   /conversations                       Create conversation
GET    /conversations?user_id=              List conversations (newest first)
DELETE /conversations/{id}?user_id=         Delete conversation + messages
POST   /conversations/{id}/messages         Send message → Gemini RAG reply
GET    /conversations/{id}/messages         Message history
GET    /health                              Liveness probe
```

Interactive API docs at `http://localhost:8000/docs` when running locally.

---

## Key Design Decisions

- **Content-addressed blobs** — files stored as `uploads/<sha256>.<ext>`; same file uploaded by multiple users stored once; GC on last-user delete
- **Per-user dedup + version chains** — each user has an independent version chain; uploading a new file version doesn't affect other users' copies
- **WAL mode + NullPool** — SQLite WAL for concurrent reads; NullPool ensures background embedding tasks always read latest committed data
- **FTS5 content table** — chunk text indexed without duplication; three sync triggers keep FTS consistent with the chunks table
- **Graceful degradation** — every Gemini API call is wrapped in try/except; system falls back to FTS5-only search on API errors or rate limits (HTTP 429)
