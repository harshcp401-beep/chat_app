from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas import MessageOut
from ..services import attachment_service
from ..websocket_manager import manager

router = APIRouter(prefix="/attachments", tags=["attachments"])


@router.post("/{other_user_id}", response_model=MessageOut, status_code=201)
async def upload_attachment(
    other_user_id: int,
    file: UploadFile = File(...),
    caption: str = Form(default="", max_length=2000),
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageOut:
    try:
        message = await attachment_service.save_attachment_message(
            db,
            me.id,
            other_user_id,
            file,
            caption,
        )
    finally:
        await file.close()
    payload = {
        "type": "message",
        "message": MessageOut.model_validate(message).model_dump(mode="json"),
    }
    await manager.send_to_user(other_user_id, payload)
    return MessageOut.model_validate(message)


@router.get("/{attachment_id}")
def download_attachment(
    attachment_id: int,
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    attachment, path = attachment_service.get_authorized_attachment(db, me.id, attachment_id)
    return FileResponse(
        path,
        filename=attachment.display_name,
        media_type=attachment.media_type,
        content_disposition_type="attachment",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
