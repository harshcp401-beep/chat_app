from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas import MessageOut
from ..services import chat_service

router = APIRouter(prefix="/messages", tags=["messages"])


@router.get("/{other_user_id}", response_model=list[MessageOut])
def conversation(other_user_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """History between ME (from the token) and other_user_id."""
    return chat_service.get_conversation(db, me.id, other_user_id)


@router.post("/{other_user_id}/read", status_code=204)
def mark_read(
    other_user_id: int,
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Mark messages from this user as seen by ME."""
    chat_service.mark_conversation_read(db, me.id, other_user_id)
    return Response(status_code=204)
