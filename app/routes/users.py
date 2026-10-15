from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas import JoinResponse, UserJoin, UserOut, UserSearchOut
from ..services import chat_service
from ..websocket_manager import manager

router = APIRouter(prefix="/users", tags=["users"])


def to_user_out(user: User) -> UserOut:
    return UserOut(id=user.id, username=user.username, is_online=manager.is_online(user.id))

# """Register a username (or resume it) and return the secret token."""
@router.post("", response_model=JoinResponse)
def join(payload: UserJoin, db: Session = Depends(get_db)):
    """Register a username (or resume it) and return the secret token."""
    user = chat_service.join_user(db, payload.username, payload.token)
    return JoinResponse(user=to_user_out(user), token=user.token)


@router.get("", response_model=list[UserOut])
def all_users(me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Only users with whom ME has an existing conversation."""
    unread_counts = chat_service.get_unread_counts(db, me.id)
    conversations = chat_service.list_conversations(db, me.id)
    return [
        UserOut(
            id=user.id,
            username=user.username,
            is_online=manager.is_online(user.id),
            unread_count=unread_counts.get(user.id, 0),
            last_message_id=last_message_id,
        )
        for user, last_message_id in conversations
    ]


@router.get("/search", response_model=list[UserSearchOut])
def search(
    q: str = Query(min_length=1, max_length=20),
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Search registered users without exposing conversation metadata."""
    return [
        UserSearchOut(id=user.id, username=user.username, is_online=manager.is_online(user.id))
        for user in chat_service.search_users(db, me.id, q)
    ]


@router.get("/online", response_model=list[UserOut])
def online_users(me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Online users with whom ME already has a conversation."""
    unread_counts = chat_service.get_unread_counts(db, me.id)
    return [
        UserOut(
            id=user.id,
            username=user.username,
            is_online=True,
            unread_count=unread_counts.get(user.id, 0),
            last_message_id=last_message_id,
        )
        for user, last_message_id in chat_service.list_conversations(db, me.id)
        if manager.is_online(user.id)
    ]
