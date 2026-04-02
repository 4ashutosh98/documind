"""
SQLAlchemy ORM model for the ``conversations`` table.

A conversation is a persistent chat thread owned by one user.  It groups a
sequence of user + assistant message pairs and surfaces them in the sidebar.

The title is auto-derived from the first 60 characters of the first user
message.  updated_at is bumped on every new message so conversations can be
sorted newest-first.
"""
from sqlalchemy import Column, String, DateTime, Index
from sqlalchemy.orm import relationship
from database import Base


class Conversation(Base):
    """
    One chat thread in the DocuMind interface.

    Columns
    -------
    id : str (UUID)
        Primary key.
    user_id : str
        Owner of this conversation.  Scopes all read/delete operations.
    title : str
        Short label shown in the sidebar.  Auto-set from the first 60 chars
        of the first user message; updated if still "New conversation".
    created_at : datetime
        Set once when the conversation is created.
    updated_at : datetime
        Bumped to now on every new message.  Used to sort conversations
        newest-first in the sidebar list.

    Relationships
    -------------
    messages : list[Message]
        All turns in this conversation.  Cascade delete removes messages
        when the conversation is deleted.
    """

    __tablename__ = "conversations"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False)
    title = Column(String, nullable=False)      # auto-set from first user message
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)   # bumped on every new message

    # Cascade all-delete-orphan: deleting a conversation removes all its messages
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")

    __table_args__ = (
        # Fast lookup for GET /conversations?user_id= (most common read pattern)
        Index("ix_conversations_user_id", "user_id"),
    )
