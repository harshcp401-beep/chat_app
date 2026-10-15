"""Database tables. Each class = one table, each attribute = one column."""
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    """Current UTC time (stored without timezone info; we always treat it as UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(20))            # as typed, e.g. "User A"
    username_key: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # lowercase, for uniqueness
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)         # secret "password" for this session
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    receiver_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    attachment: Mapped["Attachment | None"] = relationship(
        back_populates="message",
        uselist=False,
    )

    # Speeds up "give me the conversation between X and Y".
    __table_args__ = (Index("ix_messages_pair", "sender_id", "receiver_id"),)


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), unique=True)
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    receiver_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    display_name: Mapped[str] = mapped_column(String(255))
    storage_name: Mapped[str] = mapped_column(String(80), unique=True)
    media_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    message: Mapped[Message] = relationship(back_populates="attachment")


class ConversationRead(Base):
    __tablename__ = "conversation_reads"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    peer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    last_read_message_id: Mapped[int] = mapped_column(default=0)
