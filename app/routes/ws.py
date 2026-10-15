"""The WebSocket endpoint: the real-time part."""
import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from ..database import SessionLocal
from ..schemas import IncomingMessage, MessageOut
from ..services import chat_service
from ..services.chat_service import ChatError
from ..websocket_manager import manager

router = APIRouter()


@router.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int, token: str = Query(default="")):
    await websocket.accept()

    # 1) Prove identity. The user_id in the URL alone is NOT trusted; the token must match.
    with SessionLocal() as db:
        user = chat_service.authenticate_ws(db, user_id, token)
    if user is None:
        await websocket.close(code=4401)  # 4401 = our own "unauthorized" code
        return

    await manager.connect(user_id, websocket)
    await manager.broadcast({"type": "presence"})  # tell everyone the user list changed

    try:
        while True:
            raw = await websocket.receive_text()

            # 2) Validate what the browser sent.
            try:
                incoming = IncomingMessage.model_validate(json.loads(raw))
            except (json.JSONDecodeError, ValidationError):
                await websocket.send_json({"type": "error", "detail": "Invalid message."})
                continue

            # 3) Save to the database. The SENDER is always the authenticated user_id.
            with SessionLocal() as db:
                try:
                    saved = chat_service.save_message(db, user_id, incoming.receiver_id, incoming.content)
                except ChatError as exc:
                    await websocket.send_json({"type": "error", "detail": exc.detail})
                    continue
                payload = {"type": "message", "message": MessageOut.model_validate(saved).model_dump(mode="json")}

            # 4) Deliver ONLY to the receiver (if online) and echo back to the sender.
            await manager.send_to_user(incoming.receiver_id, payload)
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(user_id, websocket)
        await manager.broadcast({"type": "presence"})
