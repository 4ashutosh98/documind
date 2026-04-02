# DocuMind — End-to-End Flow

Complete numbered walkthrough of what happens from server start to the user getting an AI-generated answer, with the exact file and function responsible for each step and how it affects the user experience.

---

## Phase 0 — Application Startup

> **User experience:** Nothing visible yet. The backend is booting up.

**0.1** `docker-compose up --build` starts the Python container.
- File: `docker-compose.yml`
- Sets env vars: `OLLAMA_URL=http://host.docker.internal:11434`, `ENABLE_EMBEDDINGS=true`, etc.
- Mounts a named Docker volume (`documind_data`) at `/data` for SQLite + blobs + ChromaDB.
  - *Why a named volume?* The project lives on Google Drive (macFUSE filesystem) which lacks the `fcntl` byte-range locking SQLite WAL mode requires. A named volume lives in Docker's own storage, avoiding `"disk I/O error"` on every commit.

**0.2** Uvicorn starts and imports `main.py`.
- File: `backend/main.py`
- Creates the `FastAPI` app, registers CORS middleware (allows `localhost:3000`), and mounts all five API routers.

**0.3** The `@app.on_event("startup")` handler fires → calls `init_db()`.
- File: `backend/database.py → init_db()`
- Imports all four ORM model modules so SQLAlchemy registers their table definitions with `Base.metadata`.
- Calls `Base.metadata.create_all()` → creates four tables if they don't exist: `artifacts`, `chunks`, `conversations`, `messages`.
- Executes four raw SQL statements one by one:
  - Creates `chunks_fts` — an FTS5 virtual content table mirroring `chunks.text`.
  - Creates three sync triggers (`chunks_fts_insert`, `chunks_fts_delete`, `chunks_fts_update`) that keep the FTS index consistent with the base table automatically.
- Runs an additive `ALTER TABLE artifacts ADD COLUMN embedding_status` (no-op if column exists).
- **UX effect:** The `/health` endpoint now returns `{"status": "ok"}` and Docker's healthcheck turns green. The API is ready to accept requests.

**0.4** SQLite engine is configured with WAL mode and NullPool.
- File: `backend/database.py → _get_engine()`
- `journal_mode=WAL`: lets readers and the single writer run concurrently. Without this a background embedding task holding a write lock would block every HTTP request.
- `NullPool`: every `SessionLocal()` call opens a brand-new OS connection so it always reads the latest committed state. Without this, a pooled connection opened before the embedding background task commits would keep seeing the pre-commit snapshot — the frontend would never see `embedding_status` change from `"pending"` to `"ready"`.

**0.5** ChromaDB is **not** initialised yet — it uses lazy singletons.
- File: `backend/storage/vector_store.py`
- `_chunks_store` and `_questions_store` are `None` at startup. They are created on the first call to `_get_chunks_store()` / `_get_questions_store()`, which happens only when the first background embedding task runs.
- **UX effect:** Startup is fast regardless of ChromaDB state.

---

## Phase 1 — User Opens the App

> **User experience:** A landing page with the DocuMind wordmark and a Sign In button.

**1.1** Browser navigates to `http://localhost:3000`.
- File: `frontend/app/page.tsx`
- On mount, `useEffect` calls `getUser()` which reads `localStorage["user_id"]`.
- If already logged in → immediate redirect to `/chat` (returning users skip login).
- If not logged in → renders the landing page with Sign In button and a "Reset all data" dev button.

**1.2** User clicks **Sign In** → navigates to `/login`.
- File: `frontend/app/login/page.tsx`
- Three user cards are rendered: User 1, User 2, User 3.
- Clicking a card calls `setUser("user1")` → `localStorage.setItem("user_id", "user1")`.
- `router.push("/chat")` navigates to the main interface.
- **UX effect:** The user is now "logged in". All subsequent API calls include `user_id=user1` as a query param or form field. This is mock auth — there are no passwords, sessions, or JWTs.

---

## Phase 2 — Chat Page Loads

> **User experience:** The three-column layout appears — conversations on the left, chat in the centre, files on the right.

**2.1** `chat/page.tsx` mounts. Auth guard reads `localStorage["user_id"]`. If missing → redirect to `/login`.

**2.2** Two parallel API calls fire immediately:
- `listConversations(userId)` → `GET /artifacts?user_id=user1`
  - File: `backend/api/chat.py → list_conversations()`
  - Queries `Conversation` table filtered by `user_id`, ordered by `updated_at` DESC.
  - First time: returns `[]`.
- `listArtifacts(userId)` → `GET /artifacts?user_id=user1`
  - File: `backend/api/artifacts.py → list_artifacts()`
  - Queries `Artifact` table filtered by `user_id`, ordered by `upload_timestamp` DESC.
  - First time: returns `[]`.

**2.3** Sidebar renders empty states: "No conversations yet" and "No files yet. Upload PDFs, DOCX or XLSX to get started."

**2.4** An SSE effect watches the `artifacts` array. If any artifact has `embedding_status === "pending"` and no SSE connection is open, it opens an `EventSource` to `GET /artifacts/stream?user_id=`. The server pushes `data` events every 1 second while any artifact is pending, sends `event: done` when all are ready, then closes the connection. The `EventSource` is stored in `sseRef` and closed on unmount or when all artifacts are done.
- File: `frontend/app/chat/page.tsx` (lines ~98–120)
- **UX effect:** The sidebar file list updates automatically without a page refresh — the yellow triangle turns green within 1 second of embedding completing.

---

## Phase 3 — File Upload

> **User experience:** User drops a PDF into the upload modal. A progress bar fills, then the file appears in the sidebar with a yellow ⚠ triangle (indexing in progress).

### 3.1 — Frontend: File selection

**3.1.1** User clicks **Upload** → `FileUploadModal` opens.
- File: `frontend/components/FileUploadModal.tsx`
- Accepts PDF, DOCX, XLSX. Drag-and-drop or file picker.

**3.1.2** User selects a file. `handleFilesSelected()` fires in `chat/page.tsx`.
- A "phantom" entry is added to `uploadingFiles` immediately — the file appears in the sidebar with a progress bar before the upload even starts. User sees instant feedback.

**3.1.3** `uploadFile(file, userId, onProgress)` is called.
- File: `frontend/lib/api.ts → uploadFile()`
- Uses `XMLHttpRequest` (not `fetch`) so `onprogress` events update the progress bar in real time.
- Sends `POST /upload` with `multipart/form-data`: the file bytes + `user_id` field.

### 3.2 — Backend: Hash + Deduplication checks

**3.2.1** `upload_file()` endpoint receives the request.
- File: `backend/api/upload.py → upload_file()`
- Reads all bytes into memory with `await file.read()`.
- Computes `file_hash = hashlib.sha256(content).hexdigest()`.
  - **Why in memory before disk?** The hash must be computed before writing so we know whether to skip the write entirely (content-addressed storage).

**3.2.2** `artifact_store.check_duplicate(db, user_id, file_hash)` — exact duplicate check.
- File: `backend/storage/artifact_store.py → check_duplicate()`
- Queries `WHERE user_id = ? AND file_hash = ?` (covered by `ix_artifacts_user_hash`).
- **If duplicate found:** bumps `last_seen`, optionally re-triggers embedding if `embedding_status == "none"`, returns `UploadResponse(status="duplicate")` immediately. No new record created, no re-parsing.
- **If not found:** continues to version detection.

**3.2.3** `artifact_store.get_latest_version_by_filename(db, user_id, filename)` — version chain check.
- File: `backend/storage/artifact_store.py → get_latest_version_by_filename()`
- Queries `WHERE user_id = ? AND filename = ?` ordered by `version_number DESC`.
- If a prior version exists: `version_number = prev.version_number + 1`, `parent_id = prev.id`.
- If not: `version_number = 1`, `parent_id = None`.

### 3.3 — Blob storage (content-addressed)

**3.3.1** Blob path: `data/uploads/<sha256>.<ext>` (e.g. `data/uploads/abc123.pdf`).
- File: `backend/api/upload.py` (lines ~201–205)
- `if not blob_path.exists(): blob_path.write_bytes(content)` — skip write if already stored by another user.
- **Why content-addressed?** Two users uploading identical files share one physical blob. Disk space is proportional to unique files, not total uploads.

**3.3.2** `artifact_store.find_existing_metadata(db, file_hash)` — cross-user metadata reuse.
- File: `backend/storage/artifact_store.py → find_existing_metadata()`
- Queries any artifact (any user) with this `file_hash`. If found, returns their `extracted_metadata` dict.
- If metadata found → skip parsing entirely. Build `ParseResult` from existing metadata + copy chunks from that user.
- `source_chunk_ids` is set → used later by the embedding fast path.
- **UX effect:** Second user uploading the same file gets instant ingestion (no Docling processing), and their embedding can copy vectors instead of calling Ollama.

### 3.4 — Parsing (if fresh file)

**3.4.1** `parse_file(blob_path, file_hash, size_bytes)` routes to the correct parser.
- File: `backend/ingestion/dispatcher.py → parse_file()`
- `.pdf` / `.docx` → `DoclingParser` | `.xlsx` → `XlsxParser`

**For PDF/DOCX — `DoclingParser.parse()`:**
- File: `backend/ingestion/docling_parser.py`

  **3.4.2** `_converter.convert(str(path))` — Docling runs the full layout pipeline:
    - **PyPdfium2** backend renders each page.
    - **DocLayNet** model (trained on 80,000+ annotated documents) classifies every region: heading, paragraph, table, figure, list item. This is the key difference from font-size heuristics — Docling understands document layout, not just text.
    - **TableFormer** (accurate mode) reconstructs multi-header tables into clean markdown tables.
    - `generate_picture_images=True` crops figure bounding boxes into PIL images (stored for VLM descriptions later).

  **3.4.3** `doc.export_to_markdown()` — Docling serialises the structured output to clean markdown with correct heading hierarchy, table formatting, and `<!-- image -->` placeholders where figures appear.

  **3.4.4** Metadata extraction:
    - `page_count = len(result.pages)`
    - `title = getattr(doc.meta, "title") or _extract_first_heading(markdown)`
    - `headings = _extract_headings(markdown)[:50]` — up to 50 headings stored for the detail modal
    - If `enable_image_description`: figure images are compressed (512px max, JPEG quality 50) and stored as base64 in `extracted_metadata["figures_b64"]`. This keeps the synchronous upload fast while deferring VLM calls to the background task.

**For XLSX — `XlsxParser.parse()`:**
- File: `backend/ingestion/xlsx_parser.py`

  **3.4.5** `pd.ExcelFile(path)` opens the workbook. Each sheet is parsed with `dtype=str` to preserve numeric formatting.

  **3.4.6** Each data row is serialised as `"Col: Val | Col: Val | …"` — self-describing text that embeds well and reads naturally in search results.

  **3.4.7** Returns `xlsx_rows` (one dict per row) plus metadata: `sheet_names`, `sheet_row_counts`, `sheet_column_counts`, `total_rows`.

### 3.5 — Chunking

**3.5.1** `chunk_result(parse_result)` routes to the correct chunker.
- File: `backend/chunking/dispatcher.py → chunk_result()`

**For PDF/DOCX — `MarkdownChunker.chunk()`:**
- File: `backend/chunking/markdown_chunker.py`

  **3.5.2** `_build_heading_index(markdown)` — one-pass regex scan extracts every heading's character position and level. This index is used to assign provenance to each chunk without re-scanning.

  **3.5.3** `RecursiveCharacterTextSplitter.create_documents([markdown])` splits the text:
    - Tries each separator in order: `\n# `, `\n## `, `\n### `, … `\n\n`, `\n`, ` `, `""`
    - Chunks are at most `chunk_max_tokens=300` whitespace tokens (≈1,200 chars), with `chunk_overlap_tokens=50` overlap to avoid context loss at boundaries.
    - `add_start_index=True` records each chunk's `char_start` in the source markdown.

  **3.5.4** For each split document, `_heading_at(heading_index, char_start)` walks the heading stack to find `section` (nearest heading) and `breadcrumb` (ancestor chain like `"Chapter 1 > Section 2"`).

  **3.5.5** Each `ChunkRecord` is created with:
    - `text`: the chunk content
    - `chunk_type`: `"heading"` (starts with `#`) or `"text"`
    - `provenance`: `{section, breadcrumb, char_start, char_end}`
    - `token_count`: whitespace-split count for display

**For XLSX — `XlsxChunker.chunk()`:**
- File: `backend/chunking/xlsx_chunker.py`

  **3.5.6** Groups rows by sheet. For each sheet, creates overlapping windows of 10 rows (2-row overlap → 8-row step).

  **3.5.7** Each chunk is prefixed with `"Columns: Col1 | Col2 | …"` so it is self-describing even when retrieved without surrounding context.

  **3.5.8** Provenance: `{sheet, row_start, row_end, section}` — enables source badges like "Sheet: Revenue · Rows 10–19".

### 3.6 — Contextual Enrichment (optional, sync)

**3.6.1** `await enrich_chunks(chunks, filename, file_type, doc_start)` runs before the response is sent.
- File: `backend/chunking/enricher.py → enrich_chunks()`
- If `enable_contextual_enrichment=True` (default), for each chunk:
  - Calls Ollama (`gemma3:4b`) with a prompt: "given this document, write 1-2 sentences situating this chunk in the broader document."
  - Prepends `[Metadata] filename: X | type: pdf | section: Y\n[Context] <LLM output>\n[Content] <original text>` to the chunk text.
  - **Why?** Standalone chunks can be cryptic — "Revenue grew 122%" has no context. The enriched chunk carries its own context: "From Nvidia's Q3 2024 10-Q. Revenue grew 122%…". This significantly improves retrieval quality.
  - Graceful degradation: `except Exception: pass` — if Ollama is unavailable, the chunk text is unchanged and the upload continues.

### 3.7 — Persistence (sync, before response is sent)

**3.7.1** `artifact_store.create_artifact(db, user_id, parse_result, version_number, parent_id)`.
- File: `backend/storage/artifact_store.py → create_artifact()`
- Inserts one row into `artifacts` with a new UUID, all metadata fields, and `embedding_status="none"`.

**3.7.2** `chunk_store.bulk_insert(db, artifact.id, chunk_records)`.
- File: `backend/storage/chunk_store.py → bulk_insert()`
- Inserts all chunks in a single `db.add_all()` + `db.commit()` transaction.
- **The three FTS5 sync triggers fire automatically for every inserted row:**
  - `chunks_fts_insert` → each chunk's text is indexed into `chunks_fts`.
  - **UX effect: keyword search is available immediately after this commit** — the user could already run a query and get results before embedding even starts.

**3.7.3** `artifact.embedding_status = "pending"` → `db.commit()`.
- Sets the status synchronously *before* the background task starts so the SSE stream (`GET /artifacts/stream`) immediately pushes a `data` event with `embedding_status="pending"`, showing the yellow triangle.

**3.7.4** `background_tasks.add_task(_embed_in_background, …)` schedules the background work.
- FastAPI's `BackgroundTasks` runs this in the same thread pool after the response is sent. The upload endpoint returns immediately.

**3.7.5** `UploadResponse(artifact_id, status="created", version_number=1, message="…")` is returned.
- **UX effect:** The frontend receives the response, removes the phantom entry, calls `refreshArtifacts()`, and the real artifact appears in the sidebar with a yellow triangle icon.

---

## Phase 4 — Background Embedding

> **User experience:** The yellow triangle is visible. After 10–60 seconds (depending on file size and Ollama speed) it turns into a green checkmark. No user action required.

### 4.1 — Fast path: Copy vectors from another user (dedup)

**4.1.1** If `source_chunk_ids` is set (same file was already uploaded and indexed by another user):
- File: `backend/api/upload.py → _embed_in_background()` (lines ~71–79)
- Calls `copy_chunk_embeddings(source_chunk_ids, target_chunk_ids, …)`.
  - File: `backend/storage/vector_store.py → copy_chunk_embeddings()`
  - Reads raw embedding vectors directly from ChromaDB's internal collection using `._collection.get(ids=source_chunk_ids, include=["embeddings", "documents", "metadatas"])`.
  - Upserts the vectors under new IDs with the target user's metadata — **zero Ollama calls**.
  - Also copies Doc2Query question vectors if `enable_doc2query=True`.
- **UX effect:** Embedding completes in under a second. The green checkmark appears almost immediately after upload for duplicate files.

### 4.2 — Slow path: Fresh embedding via Ollama

**4.2.1** `embed_and_index(chunks, chunk_ids, artifact_id, user_id, filename, figures_b64)`.
- File: `backend/ingestion/embedder.py → embed_and_index()`

**4.2.2** VLM figure descriptions (if `enable_image_description=True` and `figures_b64` is non-empty):
- `_describe_figures(figures_b64)` calls `POST {ollama_url}/api/generate` with `model=qwen3-vl:4b` and `"think": false` for each compressed figure image.
- Returns `{figure_index: description_text}`.
- **Why in background?** VLM calls for a figure-heavy PDF can take 20–60 seconds — doing this synchronously would time out the upload request.

**4.2.3** For each chunk (in document order):
- If the chunk contains `<!-- image -->` placeholders and VLM descriptions are available:
  - `text_for_embedding = chunk.text.split("<!-- image -->")` → reassemble with `[Figure N: <desc>]` injected inline.
  - `global_image_counter` tracks position across all chunks so descriptions map to the correct figures.
  - **The SQLite text is unchanged** — `<!-- image -->` stays in `chunks.text` for display. Only the ChromaDB text gets the description. This keeps ingestion idempotent.

**4.2.4** `upsert_chunk(chunk_id, text_for_embedding, artifact_id, user_id, filename)`.
- File: `backend/storage/vector_store.py → upsert_chunk()`
- Calls `_get_chunks_store().add_texts([text], metadatas=[{chunk_id, artifact_id, user_id, filename}], ids=[chunk_id])`.
- LangChain Chroma internally calls `OllamaEmbeddings.embed_documents([text])` → `POST /api/embeddings` with `model=nomic-embed-text` → returns a 768-dim float vector.
- The vector + text + metadata are upserted into the `chunks` ChromaDB collection (cosine space, HNSW index).

**4.2.5** Doc2Query (if `enable_doc2query=True`):
- `_generate_questions(chunk_text, n=3)` calls Ollama (`gemma3:4b`) with a prompt to generate 3 hypothetical questions whose answer is in this chunk.
  - Example output for a revenue chunk: "What was Nvidia's Q3 2024 revenue?", "How much did revenue grow year-over-year?", "What was the YoY growth percentage?"
- `upsert_questions(chunk_id, questions, …)` embeds each question individually into the `questions` ChromaDB collection.
- **Why?** A user asking "What was Nvidia's revenue last quarter?" is much closer in embedding space to a pre-generated question than to the dense financial prose of the actual chunk. This significantly increases recall for natural-language questions.

**4.2.6** Returns `success` count (number of chunks that were successfully embedded).

### 4.3 — Status update + feature tracking

**4.3.1** Back in `_embed_in_background()`:
- If `embedded > 0`: `artifact.embedding_status = "ready"`.
- If `embedded == 0` (Ollama was unreachable): stays `"pending"` — honest state, user can retry.
- `indexed_features = {doc2query, contextual_enrichment, image_description}` is written into `extracted_metadata` JSON so the UI knows which features actually ran.
- `db.commit()`.

**4.3.2** The SSE stream (`GET /artifacts/stream`) pushes a `data` event within 1 second of the `db.commit()`. The frontend receives the updated artifact list with `embedding_status="ready"` and replaces the yellow triangle with a green checkmark. The server then sends `event: done` and closes the connection.
- **UX effect:** The artifact detail modal will now show the "Fully indexed" badge and the feature pills (Context ✦, Doc2Query ❓, Vision 👁) corresponding to which features ran.

---

## Phase 5 — Starting a Conversation

> **User experience:** User clicks "New Chat". The chat area activates. A blinking cursor appears in the input bar.

**5.1** User clicks **New Chat**.
- File: `frontend/app/chat/page.tsx → handleNewChat()`
- `createConversation(userId)` → `POST /conversations {user_id: "user1"}`.
  - File: `backend/api/chat.py → create_conversation()`
  - Creates a `Conversation` row with `title = "New conversation"`, `created_at = now`.
  - Returns `ConversationSummary`.

**5.2** `selectConversation(conv.id)` updates the URL to `/chat?cid=<uuid>`.

**5.3** The URL change triggers the `useEffect` that calls `getMessages(activeConvId, userId)` → `GET /conversations/{id}/messages?user_id=user1`.
- Returns `[]` (new conversation has no messages yet).
- The chat area shows "Type a message to get started".

---

## Phase 6 — Sending a Message

> **User experience:** User types "What are the key findings in this document?" and hits Enter. Their message appears immediately. A typing indicator (three bouncing dots) appears. After 5–30 seconds the assistant's answer arrives with collapsible source cards.

### 6.1 — Optimistic rendering

**6.1.1** `handleSend(text)` fires.
- File: `frontend/app/chat/page.tsx → handleSend()`
- An optimistic `MessageResponse` is constructed with a `temp-{timestamp}` ID and appended to `messages` state **immediately** — before any API call.
- **UX effect:** The user's message appears instantly. `setSending(true)` shows the typing indicator.

### 6.2 — API call

**6.2.1** `sendMessage(activeConvId, userId, text)` → `POST /conversations/{id}/messages`.
- File: `backend/api/chat.py → send_message()`

### 6.3 — Conversation history

**6.3.1** Load prior messages for this conversation (ascending `created_at`).

**6.3.2** `_build_history(prior_messages, limit=4)` — formats the last 4 turns as plain text:
```
Conversation so far:
User: What is the project about?
Assistant: This document describes a real-time IoT heart monitoring system…
User: What sensors does it use?
Assistant: The system uses a CC3200 LaunchPad, MAX30100 pulse oximeter, and AD8232 ECG sensor…
```
- Assistant turns are truncated to 1,000 chars to avoid blowing the model's context window.
- Returned as `history_text` injected into the RAG prompt.

**6.3.3** The user message is saved to the DB (`role="user"`, `content=text`, `query_results=None`).

**6.3.4** If this is the first message, the conversation title is set from the first 60 chars of the user's text.

### 6.4 — Hybrid retrieval

**6.4.1** `hybrid_search.search(db, q, user_id, artifact_ids=None, limit=5)`.
- File: `backend/retrieval/hybrid_search.py → search()`

**6.4.2** `fts_search(db, q, user_id, artifact_ids, limit=5)` — always runs first.
- File: `backend/retrieval/keyword_search.py → search()`
- `_build_fts_query(q)` sanitises the query — each token is wrapped in double-quotes: `"What" OR "are" OR "the" OR "key" OR "findings"`. This prevents FTS5 injection via special operators.
- Executes:
  ```sql
  SELECT c.*, a.*, -chunks_fts.rank AS score
  FROM chunks_fts
  JOIN chunks c ON chunks_fts.rowid = c.rowid
  JOIN artifacts a ON c.artifact_id = a.id
  WHERE chunks_fts MATCH '"What" OR "are" OR "the" OR "key" OR "findings"'
    AND a.user_id = 'user1'
  ORDER BY chunks_fts.rank
  LIMIT 5
  ```
- BM25 `rank` is negative (lower = better), negated so higher = better.
- `_compute_match_positions(chunk_text, q)` uses `re.finditer` on the raw query tokens to compute `[(start, end)]` char offsets — the frontend uses these for text highlighting.

**6.4.3** Check `settings.enable_embeddings`. If False → tag all FTS results `search_type="keyword"` and return early.

**6.4.4** `_ready_artifact_ids(db, user_id, artifact_ids)` — query which artifacts have `embedding_status='ready'`.
- If none ready → tag FTS results `"keyword"`, return early (embedding still in progress).

**6.4.5** `rewrite_query(q)` — optional query rewriting.
- File: `backend/retrieval/query_transformer.py → rewrite_query()`
- If `enable_query_rewriting=True`: calls Ollama (`gemma3:4b`, 15s timeout) to rewrite "What are the key findings?" → "key findings summary results conclusions".
- Falls back to original query on any failure.
- **Why rewrite for semantic but not FTS?** FTS BM25 works best with the user's exact words. Semantic embeddings benefit from a cleaned, expanded query that better matches how the document was written.

**6.4.6** `sem_search(db, sem_q, user_id, ready_ids, limit=5)`.
- File: `backend/retrieval/semantic_search.py → search()`
- `query_chunks(sem_q, user_id, artifact_ids=ready_ids, k=5)`:
  - File: `backend/storage/vector_store.py → query_chunks()`
  - ChromaDB filter: `{"$and": [{"user_id": "user1"}, {"artifact_id": {"$in": [...]}}]}`
    - *(Uses `$and` syntax because ChromaDB 0.5+ rejects flat dicts mixing equality and operators like `$in`.)*
  - `store.similarity_search_with_score(sem_q, k=5, filter=where)` → LangChain internally embeds the query via `OllamaEmbeddings` → `nomic-embed-text` → searches the HNSW index → returns `[(Document, cosine_distance)]`.
  - Cosine distance: 0.0 = identical, 1.0 = orthogonal.
- `query_questions(sem_q, user_id, artifact_ids=ready_ids, k=5)` — same search but against the Doc2Query hypothetical questions collection.
  - This retrieves chunks via questions that semantically match the user's query, not just the chunk text itself. Significantly improves recall for natural-language questions.
- Best distance per `chunk_id` is kept (a chunk may appear in both results).
- Filter: `dist < _DISTANCE_THRESHOLD (0.5)` — discard chunks that are not meaningfully similar.
- Fetch full chunk + artifact rows from SQLite for the top chunk IDs.
- Returns result rows with `score = 1.0 - dist` (cosine similarity, higher = better).

**6.4.7** `_rrf_merge([fts_results, sem_results], limit=5)`.
- File: `backend/retrieval/hybrid_search.py → _rrf_merge()`
- RRF formula: `score(chunk) = Σ 1 / (60 + rank_i)` across the lists that contain this chunk.
  - A chunk ranked #1 in FTS and #1 in semantic gets `1/61 + 1/61 ≈ 0.0328`.
  - A chunk ranked #1 in only one list gets `1/61 ≈ 0.0164`.
  - Chunks appearing in both lists consistently beat chunks appearing in only one.
- `appearances` dict tracks which list each chunk came from:
  - In both lists → `search_type = "hybrid"` (green badge in UI)
  - FTS only → `search_type = "keyword"` (grey badge)
  - Semantic only → `search_type = "semantic"` (purple badge)
- Returns top 5 merged and tagged results.

### 6.5 — Fallback for empty results

**6.5.1** If `query_resp.results` is empty, `_fallback_top_chunks()` fetches the first 2 chunks of each of the user's artifacts.
- File: `backend/api/chat.py → _fallback_top_chunks()`
- This ensures the LLM always has document content to work with for broad questions like "Tell me about this file."

### 6.6 — RAG answer generation

**6.6.1** `_build_context(query_resp)` formats the retrieved chunks:
```
[1] report.pdf (Section: Cost Analysis)
[Metadata] filename: report.pdf | type: docx
[Context] This section details the hardware components required...
[Content] The CC3200 LaunchPad costs $28.99. The MAX30100 sensor costs $4.50...

---

[2] report.pdf (Section: Software Cost)
...
```

**6.6.2** `_call_ollama_chat(question, context, history_text)`.
- File: `backend/api/chat.py → _call_ollama_chat()`
- LangChain LCEL chain: `_RAG_PROMPT | OllamaLLM(model="gemma3:4b", timeout=60, num_predict=8192) | StrOutputParser()`
- RAG prompt structure:
  ```
  You are a helpful document assistant. Answer using ONLY the excerpts below.
  Be concise and specific. If the answer isn't in the excerpts, say so.

  [conversation history — last 4 turns]

  Document excerpts:
  [1] ... [2] ... [3] ...

  Current question: What are the key findings in this document?

  Answer:
  ```
- `num_predict=8192` gives the model a generous budget for detailed answers.
- If Ollama returns an empty string or throws → `_format_assistant_text()` is used as fallback, returning formatted excerpts as text.

### 6.7 — Save and return

**6.7.1** Assistant message is saved to DB:
- `content = assistant_text` (LLM answer or formatted fallback)
- `query_results = query_resp.model_dump_json()` — the full QueryResponse is serialised to JSON and stored, so source cards can be re-rendered from history without re-querying.

**6.7.2** `conversation.updated_at = now` → `db.commit()`.

**6.7.3** Both messages are returned in `SendMessageResponse`.

**6.7.4** Frontend receives the response:
- Replaces the optimistic message (temp ID) with the real `user_message`.
- Appends `assistant_message` to the message list.
- `refreshConversations()` updates the sidebar title (now set from the first message).
- `setSending(false)` hides the typing indicator.
- **UX effect:** The assistant's answer appears with source cards below. Each card shows the filename, type badge, provenance (section/page/rows), and an expandable text snippet with matching terms highlighted in yellow.

### 6.8 — Source cards in the UI

**6.8.1** `AssistantMessage` renders source cards collapsed under a "N sources" toggle.
- File: `frontend/components/AssistantMessage.tsx`

**6.8.2** Each `SourceCard` shows:
- File: `frontend/components/SourceCard.tsx`
- File type badge (red=PDF, blue=DOCX, green=XLSX) + filename.
- Provenance: "Section: Cost Analysis · chars 30697–30699".
- Search type badge: grey=FTS, purple=semantic, green=hybrid.
- Click to expand → full chunk text with `HighlightedText` rendering yellow spans at `match_positions` offsets.
  - File: `frontend/components/HighlightedText.tsx`
  - Splits chunk text at char offsets, wraps matched spans in `<mark>` with yellow background.
- Score displayed at bottom (RRF score or cosine similarity).

---

## Phase 7 — Artifact Detail Modal

> **User experience:** User clicks a filename in the right sidebar to inspect how the document was parsed.

**7.1** `onClick={() => setDetailArtifactId(art.id)}` → `ArtifactDetailModal` mounts.
- File: `frontend/components/ArtifactDetailModal.tsx`

**7.2** `getArtifact(artifactId)` → `GET /artifacts/{id}`.
- File: `backend/api/artifacts.py → get_artifact()`
- Returns `ArtifactDetail` with all chunks (ordered by `chunk_index`).

**7.3** Modal renders three sections:

**File Metadata** — from `extracted_metadata` JSON:
- PDF: title, author, page count, all headings (up to 10 shown + "…and N more")
- XLSX: sheet names, row counts per sheet, total rows

**System Info** — from artifact columns:
- Uploaded by, version (e.g. "v2 (has parent)"), hash (first 16 chars), first seen, last seen timestamps

**Chunks** — all chunks listed in order:
- Type badge (text/heading/table_row) + token count
- Provenance: section name + char range or sheet + row range
- Text preview with fade-out gradient → **click to expand** the full chunk text, chevron rotates 180°
- Only one chunk expanded at a time (clicking another collapses the previous)

**7.4** Footer:
- `StatusBadge` shows "Fully indexed" (green) / "Indexing…" (animated yellow) / "Not indexed" (grey)
- Feature pills (only when status = "ready" and the feature ran): Context ✦, Doc2Query ❓, Vision 👁
- **Re-index** button → `POST /artifacts/{id}/reembed?user_id=`
  - File: `backend/api/artifacts.py → reembed_artifact()`
  - Sets `embedding_status = "pending"`, clears stale ChromaDB vectors, re-runs full embedding pipeline in background.
  - Use case: user pulled a better Ollama model and wants to re-index with it.

---

## Phase 8 — Deletion

> **User experience:** User clicks the trash icon on a file in the sidebar.

**8.1** `handleDeleteArtifact(artifactId)` → `DELETE /artifacts/{id}?user_id=user1`.
- File: `backend/api/artifacts.py → delete_artifact()`

**8.2** Ownership check: `artifact.user_id != user_id` → 403.

**8.3** `artifact_store.delete_artifact(db, artifact)`:
- File: `backend/storage/artifact_store.py → delete_artifact()`
- SET `parent_id = NULL` on any child versions → orphans them (doesn't cascade-delete the chain).
- Count other artifacts with the same `file_hash`. If 0 → delete blob from disk. If > 0 → blob stays (another user still references it).
- `db.delete(artifact)` → SQLAlchemy cascades to all `Chunk` rows → **the FTS5 DELETE trigger fires for each chunk row** → removes them from `chunks_fts` automatically.

**8.4** `delete_artifact_vectors(artifact_id)` removes all ChromaDB vectors for this artifact from both the `chunks` and `questions` collections.
- File: `backend/storage/vector_store.py → delete_artifact_vectors()`

**8.5** `refreshArtifacts(userId)` → sidebar updates. If this was the only file, sidebar shows the "No files yet" empty state again.

---

## Phase 9 — Data Reset (Dev)

> **User experience:** User clicks "Reset all data" on the landing page.

**9.1** `handleReset()` → `POST /dev/reset`.
- File: `frontend/app/page.tsx → handleReset()`
- Confirm dialog first.

**9.2** `reset_all_data(db)`:
- File: `backend/api/dev.py → reset_all_data()`
- `DELETE FROM messages` → `DELETE FROM conversations` → `DELETE FROM chunks` (FTS DELETE triggers fire, emptying `chunks_fts`) → `DELETE FROM artifacts`.
- `shutil.rmtree(upload_dir)` → recreate empty.
- `shutil.rmtree(chroma_dir)` → wipes all vectors.
- `vs._chunks_store = None; vs._questions_store = None` → resets in-process singletons so the next embedding call creates fresh ChromaDB collections.

---

## Summary: The Full State Machine

```
FILE UPLOADED
     │
     ├─ blob written to disk (content-addressed: shared across users)
     ├─ artifact record created (embedding_status = "none")
     ├─ chunks written to SQLite
     ├─ FTS5 index updated by sync trigger  ◄─── KEYWORD SEARCH AVAILABLE
     ├─ embedding_status → "pending"  ◄─── yellow triangle in UI
     │
     │  [background thread]
     │
     ├─ (fast path) copy vectors from another user  ─┐
     │  OR                                            ├─ embedding_status → "ready"
     └─ (slow path) Ollama embeds each chunk  ────────┘  ◄─── HYBRID SEARCH AVAILABLE
                    + Doc2Query questions               ◄─── green checkmark in UI
                    + VLM figure descriptions

USER ASKS A QUESTION
     │
     ├─ optimistic render (message appears instantly)
     │
     ├─ FTS5 BM25 search (always)
     ├─ [if embedding_status = "ready"] Ollama embeds query → ChromaDB cosine search
     │    + Doc2Query questions collection search
     ├─ RRF merge (FTS + semantic) → search_type tags per result
     │
     ├─ Ollama RAG: context + history + question → answer
     │  [fallback: formatted text excerpts if Ollama unavailable]
     │
     └─ response: LLM answer + source cards with highlights + FTS/semantic/hybrid badges
```
