"""Keeps track of who is connected right now, and sends data to them."""
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        # user_id -> that user's open WebSocket. (One connection per user.)
        self.active: dict[int, WebSocket] = {}

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        old = self.active.get(user_id)
        self.active[user_id] = websocket
        if old is not None and old is not websocket:
            try:  # the same user opened a new tab: close the old connection
                await old.close(code=4000)
            except Exception:
                pass

    def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        # Only remove if it's still THIS socket (a newer one may have replaced it).
        if self.active.get(user_id) is websocket:
            del self.active[user_id]

    def is_online(self, user_id: int) -> bool:
        return user_id in self.active

    async def send_to_user(self, user_id: int, payload: dict) -> bool:
        """Send JSON to ONE user. Returns False if they are offline."""
        ws = self.active.get(user_id)
        if ws is None:
            return False
        try:
            await ws.send_json(payload)
            return True
        except Exception:
            return False

    async def broadcast(self, payload: dict) -> None:
        """Send JSON to everyone. Used ONLY for presence events, never private messages."""
        for user_id in list(self.active):
            await self.send_to_user(user_id, payload)


manager = ConnectionManager()  # one shared instance for the whole app
