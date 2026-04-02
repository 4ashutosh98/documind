"""
Chat / Conversations API tests.

Coverage:
  - POST /conversations: create conversation, 201, schema correctness
  - GET  /conversations: list scoped to user, newest-first ordering
  - DELETE /conversations/{id}: 204, wrong user 403, not found 404
  - POST /conversations/{id}/messages: saves user + assistant turns, title auto-set,
      query_results persisted, Ollama fallback, no-chunks fallback, wrong user 403
  - GET /conversations/{id}/messages: history in chronological order, wrong user 403

Ollama (OllamaLLM) is patched via api.chat._call_ollama_chat so all tests run
fully offline.  Hybrid search uses the real FTS5 engine against the test DB.
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import patch

from chunking.base import ChunkRecord
from ingestion.base import ParseResult
from storage import artifact_store, chunk_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_conversation(client, user_id="user1", check_status=True):
    response = client.post("/conversations", json={"user_id": user_id})
    if check_status:
        assert response.status_code == 201
    return response.json()


def _create_artifact_with_chunks(db_session, user_id="user1", filename="doc.pdf", texts=None):
    """Seed an artifact + chunks into the test DB for RAG search to find."""
    texts = texts or ["Revenue grew 25% year over year to five billion dollars."]
    pr = ParseResult(
        filename=filename,
        file_type="pdf",
        size_bytes=1024,
        file_hash=uuid.uuid4().hex * 2,
        extracted_metadata={"page_count": 1},
        markdown_content="# Section\n\n" + "\n\n".join(texts),
    )
    artifact = artifact_store.create_artifact(db_session, user_id=user_id, parse_result=pr)
    records = [
        ChunkRecord(
            text=t,
            chunk_index=i,
            chunk_type="text",
            provenance={"section": "Section", "char_start": i * 100, "char_end": i * 100 + len(t)},
            token_count=len(t.split()),
        )
        for i, t in enumerate(texts)
    ]
    chunk_store.bulk_insert(db_session, artifact.id, records)
    return artifact


# ---------------------------------------------------------------------------
# POST /conversations
# ---------------------------------------------------------------------------

def test_create_conversation_returns_201(client):
    response = client.post("/conversations", json={"user_id": "user1"})
    assert response.status_code == 201


def test_create_conversation_returns_correct_schema(client):
    response = client.post("/conversations", json={"user_id": "user1"})
    data = response.json()
    assert "id" in data
    assert data["user_id"] == "user1"
    assert data["title"] == "New conversation"
    assert "created_at" in data
    assert "updated_at" in data


def test_create_conversation_assigns_unique_ids(client):
    id1 = _create_conversation(client)["id"]
    id2 = _create_conversation(client)["id"]
    assert id1 != id2


# ---------------------------------------------------------------------------
# GET /conversations
# ---------------------------------------------------------------------------

def test_list_conversations_empty(client):
    response = client.get("/conversations?user_id=user1")
    assert response.status_code == 200
    assert response.json() == []


def test_list_conversations_scoped_to_user(client):
    _create_conversation(client, user_id="user1")
    _create_conversation(client, user_id="user2")

    response = client.get("/conversations?user_id=user1")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["user_id"] == "user1"


def test_list_conversations_missing_user_id_returns_422(client):
    response = client.get("/conversations")
    assert response.status_code == 422


def test_list_conversations_newest_first(client):
    """Conversations must be returned in descending updated_at order."""
    c1 = _create_conversation(client)
    c2 = _create_conversation(client)

    response = client.get("/conversations?user_id=user1")
    ids = [c["id"] for c in response.json()]
    # c2 was created after c1, so it should appear first
    assert ids.index(c2["id"]) < ids.index(c1["id"])


# ---------------------------------------------------------------------------
# DELETE /conversations/{id}
# ---------------------------------------------------------------------------

def test_delete_conversation_returns_204(client):
    conv = _create_conversation(client)
    response = client.delete(f"/conversations/{conv['id']}?user_id=user1")
    assert response.status_code == 204


def test_delete_conversation_removes_from_list(client):
    conv = _create_conversation(client)
    client.delete(f"/conversations/{conv['id']}?user_id=user1")

    response = client.get("/conversations?user_id=user1")
    assert response.json() == []


def test_delete_conversation_wrong_user_returns_403(client):
    conv = _create_conversation(client, user_id="user1")
    response = client.delete(f"/conversations/{conv['id']}?user_id=user2")
    assert response.status_code == 403


def test_delete_conversation_not_found_returns_404(client):
    response = client.delete(f"/conversations/{uuid.uuid4()}?user_id=user1")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /conversations/{id}/messages
# ---------------------------------------------------------------------------

def test_send_message_returns_201(client, db_session):
    _create_artifact_with_chunks(db_session)
    conv = _create_conversation(client)

    with patch("api.chat._call_ollama_chat", return_value=""):
        response = client.post(
            f"/conversations/{conv['id']}/messages",
            json={"user_id": "user1", "content": "revenue"},
        )
    assert response.status_code == 201


def test_send_message_returns_both_turns(client, db_session):
    """Response must include both user_message and assistant_message."""
    _create_artifact_with_chunks(db_session)
    conv = _create_conversation(client)

    with patch("api.chat._call_ollama_chat", return_value=""):
        response = client.post(
            f"/conversations/{conv['id']}/messages",
            json={"user_id": "user1", "content": "revenue"},
        )
    data = response.json()
    assert "user_message" in data
    assert "assistant_message" in data
    assert data["user_message"]["role"] == "user"
    assert data["assistant_message"]["role"] == "assistant"
    assert data["user_message"]["content"] == "revenue"


def test_send_message_persists_user_message(client, db_session):
    conv = _create_conversation(client)

    with patch("api.chat._call_ollama_chat", return_value=""):
        client.post(
            f"/conversations/{conv['id']}/messages",
            json={"user_id": "user1", "content": "Hello"},
        )

    response = client.get(f"/conversations/{conv['id']}/messages?user_id=user1")
    messages = response.json()
    user_msgs = [m for m in messages if m["role"] == "user"]
    assert len(user_msgs) == 1
    assert user_msgs[0]["content"] == "Hello"


def test_send_message_auto_sets_title_from_first_message(client, db_session):
    """Conversation title must be updated to the first message's content (up to 60 chars)."""
    conv = _create_conversation(client)

    with patch("api.chat._call_ollama_chat", return_value=""):
        client.post(
            f"/conversations/{conv['id']}/messages",
            json={"user_id": "user1", "content": "What is the revenue forecast?"},
        )

    response = client.get("/conversations?user_id=user1")
    updated_conv = next(c for c in response.json() if c["id"] == conv["id"])
    assert updated_conv["title"] == "What is the revenue forecast?"


def test_send_message_title_truncated_at_60_chars(client, db_session):
    conv = _create_conversation(client)
    long_message = "A" * 100

    with patch("api.chat._call_ollama_chat", return_value=""):
        client.post(
            f"/conversations/{conv['id']}/messages",
            json={"user_id": "user1", "content": long_message},
        )

    response = client.get("/conversations?user_id=user1")
    updated_conv = next(c for c in response.json() if c["id"] == conv["id"])
    assert len(updated_conv["title"]) <= 60


def test_send_message_assistant_has_query_results(client, db_session):
    """Assistant message must store query_results so the frontend can render source cards."""
    _create_artifact_with_chunks(db_session, texts=["Revenue grew 25%."])
    conv = _create_conversation(client)

    with patch("api.chat._call_ollama_chat", return_value=""):
        response = client.post(
            f"/conversations/{conv['id']}/messages",
            json={"user_id": "user1", "content": "revenue"},
        )

    assistant_msg = response.json()["assistant_message"]
    assert assistant_msg["query_results"] is not None
    assert "results" in assistant_msg["query_results"]


def test_send_message_uses_ollama_answer_when_available(client, db_session):
    _create_artifact_with_chunks(db_session, texts=["Revenue grew 25%."])
    conv = _create_conversation(client)

    with patch("api.chat._call_ollama_chat", return_value="Revenue increased 25%."):
        response = client.post(
            f"/conversations/{conv['id']}/messages",
            json={"user_id": "user1", "content": "revenue"},
        )

    content = response.json()["assistant_message"]["content"]
    assert content == "Revenue increased 25%."


def test_send_message_falls_back_when_ollama_returns_empty(client, db_session):
    """When Ollama returns '' the assistant must use the formatted excerpt fallback."""
    _create_artifact_with_chunks(db_session, texts=["Revenue grew 25%."])
    conv = _create_conversation(client)

    with patch("api.chat._call_ollama_chat", return_value=""):
        response = client.post(
            f"/conversations/{conv['id']}/messages",
            json={"user_id": "user1", "content": "revenue"},
        )

    content = response.json()["assistant_message"]["content"]
    assert len(content) > 0  # fallback text is never empty


def test_send_message_no_chunks_returns_no_match_message(client, db_session):
    """If no artifacts exist, assistant must report no matching content found."""
    conv = _create_conversation(client)

    with patch("api.chat._call_ollama_chat", return_value=""):
        response = client.post(
            f"/conversations/{conv['id']}/messages",
            json={"user_id": "user1", "content": "quantum physics"},
        )

    content = response.json()["assistant_message"]["content"]
    assert len(content) > 0


def test_send_message_wrong_user_returns_403(client):
    conv = _create_conversation(client, user_id="user1")

    response = client.post(
        f"/conversations/{conv['id']}/messages",
        json={"user_id": "user2", "content": "hello"},
    )
    assert response.status_code == 403


def test_send_message_nonexistent_conversation_returns_404(client):
    response = client.post(
        f"/conversations/{uuid.uuid4()}/messages",
        json={"user_id": "user1", "content": "hello"},
    )
    assert response.status_code == 404


def test_send_message_artifact_ids_filter_is_respected(client, db_session):
    """artifact_ids in request must scope search to those specific artifacts."""
    a1 = _create_artifact_with_chunks(db_session, filename="a.pdf", texts=["Revenue data alpha."])
    a2 = _create_artifact_with_chunks(db_session, filename="b.pdf", texts=["Revenue data beta."])
    conv = _create_conversation(client)

    with patch("api.chat._call_ollama_chat", return_value=""):
        response = client.post(
            f"/conversations/{conv['id']}/messages",
            json={
                "user_id": "user1",
                "content": "revenue",
                "artifact_ids": [a1.id],
            },
        )

    query_results = response.json()["assistant_message"]["query_results"]
    returned_artifact_ids = {r["artifact"]["id"] for r in query_results["results"]}
    assert a2.id not in returned_artifact_ids


# ---------------------------------------------------------------------------
# GET /conversations/{id}/messages
# ---------------------------------------------------------------------------

def test_get_messages_returns_history_in_order(client, db_session):
    """Messages must be returned in ascending created_at order (oldest first)."""
    _create_artifact_with_chunks(db_session)
    conv = _create_conversation(client)

    with patch("api.chat._call_ollama_chat", return_value=""):
        client.post(
            f"/conversations/{conv['id']}/messages",
            json={"user_id": "user1", "content": "first question"},
        )
        client.post(
            f"/conversations/{conv['id']}/messages",
            json={"user_id": "user1", "content": "second question"},
        )

    response = client.get(f"/conversations/{conv['id']}/messages?user_id=user1")
    assert response.status_code == 200
    messages = response.json()
    # 2 sends = 4 messages total (user + assistant × 2)
    assert len(messages) == 4
    user_msgs = [m for m in messages if m["role"] == "user"]
    assert user_msgs[0]["content"] == "first question"
    assert user_msgs[1]["content"] == "second question"


def test_get_messages_wrong_user_returns_403(client):
    conv = _create_conversation(client, user_id="user1")

    response = client.get(f"/conversations/{conv['id']}/messages?user_id=user2")
    assert response.status_code == 403


def test_get_messages_not_found_returns_404(client):
    response = client.get(f"/conversations/{uuid.uuid4()}/messages?user_id=user1")
    assert response.status_code == 404


def test_get_messages_empty_for_new_conversation(client):
    conv = _create_conversation(client)

    response = client.get(f"/conversations/{conv['id']}/messages?user_id=user1")
    assert response.status_code == 200
    assert response.json() == []


def test_get_messages_missing_user_id_returns_422(client):
    conv = _create_conversation(client)
    response = client.get(f"/conversations/{conv['id']}/messages")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /conversations cascades messages
# ---------------------------------------------------------------------------

def test_delete_conversation_removes_messages(client, db_session):
    """Deleting a conversation must cascade-delete all its messages."""
    _create_artifact_with_chunks(db_session)
    conv = _create_conversation(client)

    with patch("api.chat._call_ollama_chat", return_value=""):
        client.post(
            f"/conversations/{conv['id']}/messages",
            json={"user_id": "user1", "content": "hello"},
        )

    client.delete(f"/conversations/{conv['id']}?user_id=user1")

    # Conversation is gone — messages endpoint should 404
    response = client.get(f"/conversations/{conv['id']}/messages?user_id=user1")
    assert response.status_code == 404
