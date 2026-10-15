from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas import JoinResponse, UserJoin, UserOut
from ..services import chat_service
from ..websocket_manager import manager

router = APIRouter(prefix="/users", tags=["users"])


def to_user_out(user: User) -> UserOut:
    return UserOut(id=user.id, username=user.username, is_online=manager.is_online(user.id))

# """Register a username (or resume it) and return the secret token."""
@router.post("", response_model=JoinResponse)
def join(payload: UserJoin, db: Session = Depends(get_db)):
    """Register a username (or resume it) and return the secret token."""
    user = chat_service.join_user(db, payload.username, payload.token, manager.is_online)
    return JoinResponse(user=to_user_out(user), token=user.token)


@router.get("", response_model=list[UserOut])
def all_users(me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Everyone except me, with an is_online flag (used by the sidebar)."""
    unread_counts = chat_service.get_unread_counts(db, me.id)
    return [
        UserOut(
            id=user.id,
            username=user.username,
            is_online=manager.is_online(user.id),
            unread_count=unread_counts.get(user.id, 0),
        )
        for user in chat_service.list_other_users(db, me.id)
    ]

@router.get("/online", response_model=list[UserOut])
def online_users(me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Only the users who are online right now (excluding me)."""
    unread_counts = chat_service.get_unread_counts(db, me.id)
    return [
        UserOut(
            id=user.id,
            username=user.username,
            is_online=True,
            unread_count=unread_counts.get(user.id, 0),
        )
        for user in chat_service.list_other_users(db, me.id)
        if manager.is_online(user.id)
    ]
