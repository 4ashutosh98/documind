"""
Chat / Conversations endpoints — RAG-powered multi-turn chat interface.

POST   /conversations                     — create a new conversation
GET    /conversations?user_id=            — list user's conversations (newest first)
DELETE /conversations/{id}?user_id=       — delete conversation + all its messages
POST   /conversations/{id}/messages       — send a message (runs hybrid search → Gemini RAG)
GET    /conversations/{id}/messages       — return full message history

RAG pipeline (POST /conversations/{id}/messages)
-------------------------------------------------
1. Load the last 4 turns of conversation history (for context window).
2. Run hybrid search (FTS5 + semantic, or FTS5-only if not indexed) to find
   relevant chunks from the user's documents.
3. Build a context string from the top chunks (filename, location, text).
4. Call Gemini (gemini-2.0-flash) with the RAG prompt: context + history + question.
5. Fall back to formatted search results if Gemini is unreachable (returns "").
6. Save both the user message and assistant message to the DB.
7. Return both messages so the frontend can add them without a re-fetch.

Graceful degradation:
  - Gemini unavailable or rate-limited → formatted text excerpt fallback
  - No matching chunks → "no relevant content found" message
  - No artifacts uploaded → fallback surfaces first 2 chunks of each artifact
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_groq import ChatGroq
from sqlalchemy.orm import Session

_log = logging.getLogger(__name__)

from config import settings
from database import get_db
from models.artifact import Artifact
from models.chunk import Chunk
from models.conversation import Conversation
from models.message import Message
from models.schemas import (
    ConversationSummary,
    CreateConversationRequest,
    MessageResponse,
    QueryResponse,
    SendMessageRequest,
    SendMessageResponse,
)
from retrieval.hybrid_search import search
from retrieval.result_formatter import format_results

router = APIRouter(prefix="/conversations", tags=["chat"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    """Return the current UTC time as a naive datetime (SQLite stores naive datetimes)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _conv_to_schema(conv: Conversation) -> ConversationSummary:
    """Convert a Conversation ORM instance to a ConversationSummary Pydantic model."""
    return ConversationSummary.model_validate(conv)


def _msg_to_schema(msg: Message) -> MessageResponse:
    """
    Convert a Message ORM instance to a MessageResponse Pydantic model.

    Deserialises the query_results JSON column back to a QueryResponse object
    so the frontend can render source cards.  Returns None on parse failure
    rather than crashing the history load.

    Args:
        msg: Message ORM instance.

    Returns:
        MessageResponse with query_results populated for assistant messages.
    """
    query_results: QueryResponse | None = None
    if msg.query_results:
        try:
            data = json.loads(msg.query_results)
            # Validate against the schema to catch any structural mismatches
            query_results = QueryResponse.model_validate(data)
        except Exception:
            pass  # Return None — stale or corrupt JSON is non-fatal
    return MessageResponse(
        id=msg.id,
        conversation_id=msg.conversation_id,
        role=msg.role,
        content=msg.content,
        query_results=query_results,
        created_at=msg.created_at,
    )



def _fallback_top_chunks(
    db: Session, user_id: str, artifact_ids: list[str] | None
) -> list[dict]:
    """
    Return the first 2 chunks of each artifact when keyword search finds nothing.

    This ensures the assistant always has *something* to work with even when
    the user's query has no keyword matches — e.g. "What is this document about?"
    The fallback is clearly labelled in the assistant response ("here's an overview").

    Args:
        db:           SQLAlchemy session.
        user_id:      Owner — scopes the artifact lookup.
        artifact_ids: Optional list to restrict to specific artifacts.

    Returns:
        List of raw row dicts (same format as keyword_search output) with
        score=None and match_positions=[] since these are not ranked results.
        Empty list if the user has no artifacts.
    """
    art_q = db.query(Artifact).filter(Artifact.user_id == user_id)
    if artifact_ids:
        art_q = art_q.filter(Artifact.id.in_(artifact_ids))
    artifacts = art_q.limit(5).all()

    rows = []
    for art in artifacts:
        chunks = (
            db.query(Chunk)
            .filter(Chunk.artifact_id == art.id)
            .order_by(Chunk.chunk_index)
            .limit(2)
            .all()
        )
        for c in chunks:
            rows.append({
                "chunk_id": c.id,
                "artifact_id": art.id,
                "chunk_index": c.chunk_index,
                "chunk_text": c.text,
                "chunk_type": c.chunk_type,
                "provenance": c.provenance,
                "token_count": c.token_count,
                "user_id": art.user_id,
                "filename": art.filename,
                "file_type": art.file_type,
                "size_bytes": art.size_bytes,
                "file_hash": art.file_hash,
                "version_number": art.version_number,
                "parent_id": art.parent_id,
                "uploaded_by": art.uploaded_by,
                "upload_timestamp": art.upload_timestamp,
                "first_seen": art.first_seen,
                "last_seen": art.last_seen,
                "extracted_metadata": art.extracted_metadata,
                "score": None,
                "match_positions": [],
            })
    return rows


_RAG_PROMPT = PromptTemplate.from_template(
    "You are a helpful document assistant. Answer the user's question using ONLY the "
    "document excerpts provided below. Be concise and specific. If the answer is not "
    "in the excerpts, say so plainly.\n\n"
    "{history}"
    "Document excerpts:\n{context}\n\n"
    "Current question: {question}\n\n"
    "Answer:"
)


def _build_context(query_resp: QueryResponse) -> str:
    """
    Build the document context string injected into the RAG prompt.

    Formats each matched chunk as a numbered excerpt with its source location,
    separated by horizontal rules so the LLM can distinguish chunks clearly.

    Example output:
        [1] report.pdf (Chapter 2, p.14)
        Revenue grew 122% year over year to $18.1 billion...

        ---

        [2] report.pdf (Chapter 3)
        Operating expenses increased 45%...

    Args:
        query_resp: QueryResponse with matched chunks and their provenance.

    Returns:
        Multi-line string to substitute into the ``{context}`` slot of _RAG_PROMPT.
    """
    parts = []
    for i, match in enumerate(query_resp.results, 1):
        prov = match.chunk.provenance
        loc_parts: list[str] = []
        if prov.page is not None:
            loc_parts.append(f"p.{prov.page}")
        if prov.section:
            loc_parts.append(prov.section)
        if prov.sheet:
            loc_parts.append(prov.sheet)
        if prov.row_start is not None:
            loc_parts.append(f"rows {prov.row_start}–{prov.row_end}")
        loc = ", ".join(loc_parts) if loc_parts else "—"
        parts.append(
            f"[{i}] {match.artifact.filename} ({loc})\n{match.chunk.text}"
        )
    return "\n\n---\n\n".join(parts)


def _build_history(messages: list, limit: int = 4) -> str:
    """
    Format the last `limit` conversation turns (user + assistant pairs) as a
    plain-text block for the RAG prompt. Truncates long assistant replies to
    avoid blowing the model's context window.
    Returns an empty string when there are no prior messages.
    """
    recent = messages[-(limit * 2):]  # at most limit pairs
    if not recent:
        return ""
    lines = []
    for msg in recent:
        prefix = "User" if msg.role == "user" else "Assistant"
        content = msg.content[:1000] if msg.role == "assistant" else msg.content
        lines.append(f"{prefix}: {content}")
    return "Conversation so far:\n" + "\n".join(lines) + "\n\n"


def _call_llm_chat(question: str, context: str, history: str = "", groq_api_key: str = "") -> str:
    """
    Run the RAG chain: document context + history + question → LLM answer.

    Uses LangChain LCEL: PromptTemplate | ChatGroq | StrOutputParser.
    The prompt instructs the model to answer ONLY from the provided excerpts
    and to say so plainly if the answer is not there — reducing hallucination.

    Args:
        question:     The user's current question.
        context:      Formatted document excerpts (from _build_context).
        history:      Prior conversation turns (from _build_history), may be "".
        groq_api_key: User-provided key (overrides server key if set).

    Returns:
        The model's answer as a stripped string, or "" on any failure.
        Callers should fall back to _format_assistant_text when "" is returned.
    """
    resolved_key = groq_api_key or settings.groq_api_key
    _log.info("[chat] calling Groq %s (context=%d chars, history=%d chars)",
              settings.groq_model, len(context), len(history))
    try:
        chain = _RAG_PROMPT | ChatGroq(
            model=settings.groq_model,
            groq_api_key=resolved_key,
        ) | StrOutputParser()
        answer = chain.invoke({"context": context, "question": question, "history": history}).strip()
        _log.info("[chat] Groq response: %d chars", len(answer))
        return answer
    except Exception as exc:
        _log.error("[chat] Groq call failed: %s", exc)
        return ""


def _get_conversation_or_404(db: Session, conv_id: str) -> Conversation:
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


def _format_assistant_text(q: str, query_resp: QueryResponse, is_fallback: bool = False) -> str:
    """
    Build a plain-text fallback response when Ollama is unavailable.

    Formats the top search results as a structured excerpt list so the user
    still gets useful information even without an LLM answer.  The frontend
    renders source cards regardless of whether this fallback ran, so the user
    can always expand and read the raw chunks.

    Args:
        q:           The user's original query.
        query_resp:  QueryResponse from hybrid search.
        is_fallback: True when results came from _fallback_top_chunks
                     (no keyword matches — results are "overview" chunks).

    Returns:
        Markdown-formatted assistant response text.
    """
    if not query_resp.results:
        return (
            f'I searched your documents for "{q}" but found no matching content. '
            "Try uploading relevant files or rephrasing your query."
        )

    if is_fallback:
        lines = [f'No exact keyword matches for **"{q}"** — here\'s an overview of your documents:\n']
    else:
        lines = [f'Here are the most relevant excerpts for **"{q}"**:\n']

    for i, match in enumerate(query_resp.results, 1):
        prov = match.chunk.provenance
        loc_parts: list[str] = []
        if prov.page is not None:
            loc_parts.append(f"p.{prov.page}")
        if prov.section:
            loc_parts.append(prov.section)
        if prov.sheet:
            loc_parts.append(prov.sheet)
        if prov.row_start is not None:
            loc_parts.append(f"rows {prov.row_start}–{prov.row_end}")
        loc = " · ".join(loc_parts) if loc_parts else "—"

        lines.append(
            f"**{i}. {match.artifact.filename}** ({loc})\n"
            f"> {match.chunk.text[:300].strip()}"
            + ("…" if len(match.chunk.text) > 300 else "")
        )

    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", response_model=ConversationSummary, status_code=201)
def create_conversation(
    body: CreateConversationRequest,
    db: Session = Depends(get_db),
):
    now = _now()
    conv = Conversation(
        id=str(uuid.uuid4()),
        user_id=body.user_id,
        title="New conversation",
        created_at=now,
        updated_at=now,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return _conv_to_schema(conv)


@router.get("", response_model=list[ConversationSummary])
def list_conversations(
    user_id: str = Query(...),
    db: Session = Depends(get_db),
):
    convs = (
        db.query(Conversation)
        .filter(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
        .all()
    )
    return [_conv_to_schema(c) for c in convs]


@router.delete("/{conv_id}", status_code=204)
def delete_conversation(
    conv_id: str,
    user_id: str = Query(...),
    db: Session = Depends(get_db),
):
    conv = _get_conversation_or_404(db, conv_id)
    if conv.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not your conversation")
    db.delete(conv)
    db.commit()


@router.post("/{conv_id}/messages", response_model=SendMessageResponse, status_code=201)
def send_message(
    conv_id: str,
    body: SendMessageRequest,
    db: Session = Depends(get_db),
    x_groq_api_key: str = Header(default="", alias="X-Groq-Api-Key"),
    x_google_api_key: str = Header(default="", alias="X-Google-Api-Key"),
):
    conv = _get_conversation_or_404(db, conv_id)
    if conv.user_id != body.user_id:
        raise HTTPException(status_code=403, detail="Not your conversation")

    groq_key = x_groq_api_key or settings.groq_api_key
    google_key = x_google_api_key or settings.google_api_key
    _log.info("[chat] message from %s in conv %s (groq key: %s, google key: %s)",
              body.user_id, conv_id, "user" if x_groq_api_key else "server", "user" if x_google_api_key else "server")

    now = _now()

    # 1. Fetch prior messages for conversation history (before saving the new turn)
    prior_messages = (
        db.query(Message)
        .filter(Message.conversation_id == conv_id)
        .order_by(Message.created_at.asc())
        .all()
    )
    history_text = _build_history(prior_messages)

    # 2. Save user message
    user_msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=conv_id,
        role="user",
        content=body.content,
        query_results=None,
        created_at=now,
    )
    db.add(user_msg)

    # 2. Auto-set conversation title from first message
    if conv.title == "New conversation":
        conv.title = body.content[:60].strip()

    # 3. Hybrid search (FTS5 + semantic if enabled; query rewriting if enabled)
    raw = search(
        db=db,
        q=body.content,
        user_id=body.user_id,
        artifact_ids=body.artifact_ids,
        limit=5,
        groq_api_key=groq_key,
        google_api_key=google_key,
    )
    query_resp = format_results(body.content, raw)
    _log.info("[chat] search returned %d results", len(query_resp.results))

    # Fallback: if no keyword hits, surface top chunks as a document overview
    is_fallback = False
    if not query_resp.results:
        fallback_raw = _fallback_top_chunks(db, body.user_id, body.artifact_ids)
        if fallback_raw:
            query_resp = format_results(body.content, fallback_raw)
            is_fallback = True
            _log.info("[chat] no search hits — using fallback top chunks (%d)", len(query_resp.results))

    # 4. Build assistant reply — try Groq first, fall back to template
    assistant_text = ""
    if query_resp.results:
        context = _build_context(query_resp)
        assistant_text = _call_llm_chat(body.content, context, history_text, groq_key)
    if not assistant_text:
        _log.info("[chat] LLM unavailable or no results — using formatted fallback")
        assistant_text = _format_assistant_text(body.content, query_resp, is_fallback)

    # 5. Save assistant message
    assistant_msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=conv_id,
        role="assistant",
        content=assistant_text,
        query_results=query_resp.model_dump_json(),
        created_at=now,
    )
    db.add(assistant_msg)

    # 6. Update conversation timestamp
    conv.updated_at = now

    db.commit()
    db.refresh(user_msg)
    db.refresh(assistant_msg)

    return SendMessageResponse(
        user_message=_msg_to_schema(user_msg),
        assistant_message=_msg_to_schema(assistant_msg),
    )


@router.get("/{conv_id}/messages", response_model=list[MessageResponse])
def get_messages(
    conv_id: str,
    user_id: str = Query(...),
    db: Session = Depends(get_db),
):
    conv = _get_conversation_or_404(db, conv_id)
    if conv.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not your conversation")

    msgs = (
        db.query(Message)
        .filter(Message.conversation_id == conv_id)
        .order_by(Message.created_at.asc())
        .all()
    )
    return [_msg_to_schema(m) for m in msgs]
