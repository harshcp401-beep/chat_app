"""Pydantic schemas: describe and VALIDATE the data going in and out of the API."""
import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, computed_field, field_serializer, field_validator

USERNAME_RE = re.compile(r"^[A-Za-z0-9 _-]+$")


class UserJoin(BaseModel):
    """Body of POST /users."""
    username: str
    token: Optional[str] = None  # sent by the browser when resuming an existing session

    @field_validator("username")
    @classmethod
    def clean_username(cls, v: str) -> str:
        v = " ".join(v.split())  # trim and collapse repeated spaces
        if not 2 <= len(v) <= 20:
            raise ValueError("Username must be 2-20 characters long")
        if not USERNAME_RE.match(v):
            raise ValueError("Username can only contain letters, numbers, spaces, _ and -")
        return v


class UserOut(BaseModel):
    id: int
    username: str
    is_online: bool = False
    unread_count: int = 0
    last_message_id: Optional[int] = None


class UserSearchOut(BaseModel):
    id: int
    username: str
    is_online: bool = False


class JoinResponse(BaseModel):
    user: UserOut
    token: str


class IncomingMessage(BaseModel):
    """What the browser sends over the WebSocket. Note: NO sender field --
    the server already knows who you are from your authenticated connection."""
    receiver_id: int
    content: str

    @field_validator("content")
    @classmethod
    def clean_content(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Message cannot be empty")
        if len(v) > 2000:
            raise ValueError("Message is too long (max 2000 characters)")
        return v


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    display_name: str
    media_type: str
    size: int


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # allows building from a SQLAlchemy object

    id: int
    sender_id: int
    receiver_id: int
    content: str
    created_at: datetime
    attachment: Optional[AttachmentOut] = None

    @computed_field
    @property
    def kind(self) -> str:
        return self.attachment.kind if self.attachment else "text"

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return value.isoformat() + "Z"  # "Z" tells the browser this is UTC
