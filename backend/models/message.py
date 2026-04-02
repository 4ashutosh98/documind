"""
SQLAlchemy ORM model for the ``messages`` table.

A message is one turn in a conversation — either a user question or an
assistant reply.  Assistant messages carry the full QueryResponse (serialised
as JSON in query_results) so source cards can be re-rendered when the
conversation is loaded from history.

query_results design
---------------------
Storing the QueryResponse JSON on the message means:
  - Conversation history is self-contained — no re-query needed on load.
  - Source cards (filename, provenance, highlighted text) are stable even
    if the underlying artifact is later deleted or re-indexed.
  - The search_type badge ("FTS" / "semantic" / "hybrid") reflects the
    retrieval method used at message creation time, not the current state.
"""
from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Index
from sqlalchemy.orm import relationship
from database import Base


class Message(Base):
    """
    One turn (user or assistant) in a Conversation.

    Columns
    -------
    id : str (UUID)
        Primary key.
    conversation_id : str (FK → conversations.id)
        Parent conversation.  ON DELETE CASCADE removes messages when the
        conversation is deleted.
    role : str
        ``"user"`` or ``"assistant"``.
    content : str
        Display text.  For assistant messages this is the Ollama RAG answer
        (or the fallback formatted excerpt if Ollama is unreachable).
    query_results : str | None (JSON)
        Serialised QueryResponse — populated only for assistant messages.
        Contains all matched chunks, scores, provenance, and search_type badges.
        Deserialised by ``_msg_to_schema`` in api/chat.py.
    created_at : datetime
        Timestamp of when this message was saved.

    Relationships
    -------------
    conversation : Conversation
        The parent conversation.
    """

    __tablename__ = "messages"

    id = Column(String, primary_key=True)
    conversation_id = Column(
        String,
        ForeignKey("conversations.id", ondelete="CASCADE"),  # cascade delete with parent
        nullable=False,
    )
    role = Column(String, nullable=False)       # "user" | "assistant"
    content = Column(Text, nullable=False)      # the displayed message text
    # Stored QueryResponse JSON — only populated for assistant messages.
    # Contains chunks, scores, provenance, and search_type for source-card rendering.
    query_results = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False)

    conversation = relationship("Conversation", back_populates="messages")

    __table_args__ = (
        # Covers the common pattern: all messages in a conversation ordered by time
        Index("ix_messages_conversation_id", "conversation_id"),
    )
