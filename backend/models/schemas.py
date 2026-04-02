"""
Pydantic request/response schemas for all API endpoints.

These schemas are the contract between the backend and frontend — every field
here has a corresponding TypeScript interface in frontend/types/index.ts.

Schema hierarchy
-----------------
ArtifactSummary  — used in list views (sidebar) and search results
  └─ ArtifactDetail — extends summary with the full chunks list (detail modal)

QueryMatch  — one search result: chunk + parent artifact + score + positions
  └─ QueryResponse — the full result set for a query

MessageResponse  — one chat turn (user or assistant)
  └─ SendMessageResponse — the pair returned by POST /conversations/{id}/messages
"""
from __future__ import annotations
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel


class ProvenanceRef(BaseModel):
    """
    Source location of a chunk within its original document.

    All fields are optional because the schema varies by file type:
      PDF/DOCX: page, section, breadcrumb, char_start, char_end
      XLSX:     sheet, row_start, row_end, section

    page is populated from Docling's element-level page numbers: the nearest
    preceding section header's page is attributed to the chunk. This is a
    heuristic — chunks that span a page boundary are attributed to the page
    their heading started on.
    """
    page: Optional[int] = None          # PDF page number (1-based) from Docling element prov
    section: Optional[str] = None       # nearest preceding heading text
    breadcrumb: Optional[str] = None    # ancestor heading chain, e.g. "Part I > Chapter 2"
    sheet: Optional[str] = None         # XLSX sheet name
    char_start: Optional[int] = None    # character offset in source markdown
    char_end: Optional[int] = None      # character offset in source markdown
    row_start: Optional[int] = None     # XLSX data row index (0-based)
    row_end: Optional[int] = None       # XLSX data row index (0-based)


class ChunkResponse(BaseModel):
    """
    A single chunk as returned by the API.

    Carries the chunk text, its position in the artifact (chunk_index), its
    type (text/heading/table_row), and provenance for UI display.
    """
    id: str
    artifact_id: str
    chunk_index: int            # 0-based position within the artifact
    text: str                   # full chunk text (may include [Metadata]/[Context] prefixes)
    chunk_type: str             # "text" | "heading" | "table_row"
    provenance: ProvenanceRef
    token_count: Optional[int] = None   # whitespace-split token estimate

    model_config = {"from_attributes": True}


class ArtifactSummary(BaseModel):
    """
    Lightweight artifact representation used in list views and search results.

    Does not include chunks — use ArtifactDetail for the full list.
    extracted_metadata is the parsed JSON from the DB column.
    """
    id: str
    user_id: str
    filename: str
    file_type: str              # "pdf" | "docx" | "xlsx"
    size_bytes: int
    file_hash: str              # SHA-256 hex digest
    version_number: int         # starts at 1, increments on re-upload with new hash
    parent_id: Optional[str] = None     # UUID of previous version or None
    uploaded_by: str            # same as user_id in this mock auth system
    upload_timestamp: datetime
    first_seen: datetime        # set once at creation
    last_seen: datetime         # bumped on each duplicate upload
    extracted_metadata: dict    # parsed JSON: title, page_count, sheet_names, etc.
    # "none" = embeddings disabled | "pending" = indexing in progress | "ready" = hybrid search active
    embedding_status: str = "none"

    model_config = {"from_attributes": True}


class ArtifactDetail(ArtifactSummary):
    """
    Full artifact detail including all chunks.

    Returned by GET /artifacts/{id}.  Used to populate the artifact detail
    modal in the frontend, which displays metadata, system info, and each chunk.
    """
    chunks: list[ChunkResponse] = []


class UploadResponse(BaseModel):
    """
    Response returned by POST /upload.

    status values:
      "created"     — new artifact, first upload of this file by this user
      "duplicate"   — exact same file (same hash) already exists for this user
      "new_version" — same filename but different hash; version_number incremented
    """
    artifact_id: str
    status: Literal["created", "duplicate", "new_version"]
    version_number: int
    message: str


class QueryRequest(BaseModel):
    """
    Request body for POST /query.

    artifact_ids: if provided, restricts the search to those artifacts.
    If None, searches all artifacts owned by user_id.
    """
    q: str                                      # natural language or keyword query
    user_id: str
    artifact_ids: Optional[list[str]] = None    # None = search all user's artifacts
    limit: int = 10                             # max results to return


class QueryMatch(BaseModel):
    """
    A single search result containing the matched chunk and its parent artifact.

    score:          RRF score (hybrid) or BM25 negated rank (FTS-only).
                    Higher is better.  May be None for fallback results.
    match_positions: list of (start, end) char offsets within chunk.text where
                    query terms appear.  Used by the HighlightedText component.
    search_type:    which retrieval path found this chunk:
                      "keyword"  — FTS5 BM25 only
                      "semantic" — ChromaDB cosine similarity only
                      "hybrid"   — appeared in both lists (RRF merged)
    """
    chunk: ChunkResponse
    artifact: ArtifactSummary
    score: Optional[float] = None
    match_positions: list[tuple[int, int]] = []     # char offsets within chunk.text
    search_type: Optional[str] = None              # "keyword", "semantic", or "hybrid"


class QueryResponse(BaseModel):
    """
    Full response for a query, containing all matched chunks with provenance.

    Also stored as JSON in Message.query_results for assistant messages so
    source cards can be re-rendered from conversation history.
    """
    query: str          # the original query string (echoed back for display)
    total: int          # number of results returned
    results: list[QueryMatch]


class DeleteResponse(BaseModel):
    """Response returned by DELETE /artifacts/{id}."""
    artifact_id: str
    blob_deleted: bool  # True if the physical file was removed (last reference)
    message: str


# ---------------------------------------------------------------------------
# Chat / Conversations
# ---------------------------------------------------------------------------

class ConversationSummary(BaseModel):
    """
    Lightweight conversation representation used in the sidebar list.

    title is auto-set from the first 60 characters of the first user message.
    """
    id: str
    user_id: str
    title: str          # auto-derived from first message
    created_at: datetime
    updated_at: datetime    # bumped on every new message; used for sort order

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    """
    A single message in a conversation (user turn or assistant turn).

    query_results is populated only for assistant messages — it contains the
    QueryResponse used to generate the answer, serialised as JSON in the DB
    and deserialised here.  The frontend uses it to render source cards.
    """
    id: str
    conversation_id: str
    role: str                               # "user" | "assistant"
    content: str                            # message text (supports basic markdown)
    query_results: Optional[QueryResponse] = None   # assistant only; None for user messages
    created_at: datetime

    model_config = {"from_attributes": True}


class CreateConversationRequest(BaseModel):
    """Request body for POST /conversations."""
    user_id: str


class SendMessageRequest(BaseModel):
    """
    Request body for POST /conversations/{id}/messages.

    artifact_ids: if provided, restricts retrieval to those artifacts.
    Useful when the user wants to ask a question about a specific file.
    """
    user_id: str
    content: str                                    # the user's question or message
    artifact_ids: Optional[list[str]] = None        # None = search all user's artifacts


class SendMessageResponse(BaseModel):
    """
    Response for POST /conversations/{id}/messages.

    Returns both turns so the frontend can add them to the message list
    without a separate GET call.
    """
    user_message: MessageResponse
    assistant_message: MessageResponse
