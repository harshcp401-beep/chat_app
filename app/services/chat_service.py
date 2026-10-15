"""Business logic. Routes and the WebSocket handler call these functions,
so the rules live in ONE place."""
import secrets

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..models import ConversationRead, Message, User


class ChatError(Exception):
    """A problem we want to report nicely to the user."""

    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def join_user(db: Session, username: str, token: str | None) -> User:
    key = username.lower()
    user = db.scalar(select(User).where(User.username_key == key))

    if user is None:  # brand new username
        user = User(username=username, username_key=key, token=secrets.token_urlsafe(32))
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    # Existing usernames can only be resumed with their existing session token.
    if token and secrets.compare_digest(token, user.token):
        return user
    raise ChatError(
        "This username is already registered. Resume with its existing session or choose another username.",
        409,
    )


def get_user_by_token(db: Session, token: str) -> User | None:
    if not token:
        return None
    return db.scalar(select(User).where(User.token == token))


def authenticate_ws(db: Session, user_id: int, token: str) -> User | None:
    user = db.get(User, user_id)
    if user and secrets.compare_digest(token, user.token):
        return user
    return None


def search_users(db: Session, me_id: int, query: str) -> list[User]:
    normalized_query = query.strip().lower()
    if not normalized_query:
        return []
    stmt = (
        select(User)
        .where(
            User.id != me_id,
            User.username_key.contains(normalized_query, autoescape=True),
        )
        .order_by(User.username_key)
    )
    return list(db.scalars(stmt))


def list_conversations(db: Session, me_id: int) -> list[tuple[User, int]]:
    peer_id = case(
        (Message.sender_id == me_id, Message.receiver_id),
        else_=Message.sender_id,
    )
    latest_messages = (
        select(peer_id.label("peer_id"), func.max(Message.id).label("message_id"))
        .where(or_(Message.sender_id == me_id, Message.receiver_id == me_id))
        .group_by(peer_id)
        .subquery()
    )
    stmt = (
        select(User, latest_messages.c.message_id)
        .join(latest_messages, User.id == latest_messages.c.peer_id)
        .order_by(latest_messages.c.message_id.desc(), User.username_key)
    )
    return [(user, message_id) for user, message_id in db.execute(stmt)]


def get_unread_counts(db: Session, me_id: int) -> dict[int, int]:
    stmt = (
        select(Message.sender_id, func.count(Message.id))
        .outerjoin(
            ConversationRead,
            and_(
                ConversationRead.user_id == me_id,
                ConversationRead.peer_id == Message.sender_id,
            ),
        )
        .where(
            Message.receiver_id == me_id,
            or_(
                ConversationRead.last_read_message_id.is_(None),
                Message.id > ConversationRead.last_read_message_id,
            ),
        )
        .group_by(Message.sender_id)
    )
    return {sender_id: count for sender_id, count in db.execute(stmt)}


def mark_conversation_read(db: Session, me_id: int, other_id: int) -> None:
    check_other_user(db, me_id, other_id)
    latest_message_id = db.scalar(
        select(func.max(Message.id)).where(
            Message.sender_id == other_id,
            Message.receiver_id == me_id,
        )
    )
    if latest_message_id is None:
        return

    read_state = db.get(ConversationRead, (me_id, other_id))
    if read_state is None:
        db.add(
            ConversationRead(
                user_id=me_id,
                peer_id=other_id,
                last_read_message_id=latest_message_id,
            )
        )
    else:
        read_state.last_read_message_id = max(read_state.last_read_message_id, latest_message_id)
    db.commit()


def check_other_user(db: Session, me_id: int, other_id: int) -> User:
    if other_id == me_id:
        raise ChatError("You cannot chat with yourself.")
    other = db.get(User, other_id)
    if other is None:
        raise ChatError("That user does not exist.", 404)
    return other


def save_message(db: Session, sender_id: int, receiver_id: int, content: str) -> Message:
    check_other_user(db, sender_id, receiver_id)
    msg = Message(sender_id=sender_id, receiver_id=receiver_id, content=content)
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def get_conversation(db: Session, me_id: int, other_id: int, limit: int = 200) -> list[Message]:
    mark_conversation_read(db, me_id, other_id)
    stmt = (
        select(Message).options(selectinload(Message.attachment))
        .where(
            or_(
                and_(Message.sender_id == me_id, Message.receiver_id == other_id),
                and_(Message.sender_id == other_id, Message.receiver_id == me_id),
            )
        )
        .order_by(Message.id.desc())  # newest first so LIMIT keeps the latest ones
        .limit(limit)
    )
    return list(reversed(list(db.scalars(stmt))))  # flip back to oldest -> newest
