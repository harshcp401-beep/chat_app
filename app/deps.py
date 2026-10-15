"""Reusable FastAPI dependencies (things routes can ask for)."""
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .database import get_db
from .models import User
from .services import chat_service


def get_current_user(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> User:
    """Reads 'Authorization: Bearer <token>' and finds the user it belongs to."""
    scheme, _, token = authorization.partition(" ")
    user = chat_service.get_user_by_token(db, token) if scheme.lower() == "bearer" else None
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or missing token. Please join again.")
    return user
