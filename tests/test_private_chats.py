import unittest

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Attachment, Message, User
from app.routes import attachments, messages, users, ws as websocket_routes
from app.services import chat_service
from app.services.chat_service import ChatError


class PrivateChatTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as db:
            self.alice = User(username="Alice", username_key="alice", token="alice-token")
            self.bob = User(username="Bob", username_key="bob", token="bob-token")
            self.carol = User(username="Carol", username_key="carol", token="carol-token")
            db.add_all([self.alice, self.bob, self.carol])
            db.commit()

            self.bob_carol_message = Message(
                sender_id=self.bob.id,
                receiver_id=self.carol.id,
                content="Bob and Carol private message",
            )
            db.add(self.bob_carol_message)
            db.flush()
            self.attachment = Attachment(
                message_id=self.bob_carol_message.id,
                uploader_id=self.bob.id,
                receiver_id=self.carol.id,
                kind="file",
                display_name="private.txt",
                storage_name="private.txt",
                media_type="text/plain",
                size=10,
            )
            db.add(self.attachment)
            db.commit()
            self.attachment_id = self.attachment.id
            self.alice_id = self.alice.id
            self.bob_id = self.bob.id
            self.carol_id = self.carol.id

        self.app = FastAPI()
        self.app.include_router(users.router)
        self.app.include_router(messages.router)
        self.app.include_router(attachments.router)
        self.app.include_router(websocket_routes.router)
        self.app.add_exception_handler(
            ChatError,
            lambda request, exc: JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
            ),
        )

        def override_get_db():
            with Session(self.engine) as db:
                yield db

        self.app.dependency_overrides[get_db] = override_get_db
        self.original_ws_session_local = websocket_routes.SessionLocal
        websocket_routes.SessionLocal = lambda: Session(self.engine)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        websocket_routes.SessionLocal = self.original_ws_session_local
        self.engine.dispose()

    @staticmethod
    def auth(token):
        return {"Authorization": f"Bearer {token}"}

    def test_new_user_has_no_chats_despite_other_users_conversations(self):
        response = self.client.get("/users", headers=self.auth("alice-token"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_search_finds_registered_users_without_private_metadata(self):
        response = self.client.get("/users/search?q=OB", headers=self.auth("alice-token"))
        self.assertEqual(response.status_code, 200)
        results = response.json()
        self.assertEqual([result["id"] for result in results], [self.bob_id])
        self.assertEqual(set(results[0]), {"id", "username", "is_online"})
        self.assertEqual(
            self.client.get("/users/search?q=Alice", headers=self.auth("alice-token")).json(),
            [],
        )

        self.assertEqual(
            self.client.get("/users", headers=self.auth("alice-token")).json(),
            [],
        )

    def test_opening_chat_is_empty_and_first_message_adds_both_participants(self):
        history = self.client.get(
            f"/messages/{self.bob_id}",
            headers=self.auth("alice-token"),
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json(), [])

        with self.client.websocket_connect(
            f"/ws/{self.alice_id}?token=alice-token",
        ) as websocket:
            self.assertEqual(websocket.receive_json()["type"], "presence")
            for content in ("Hello", "Another message"):
                websocket.send_json({"receiver_id": self.bob_id, "content": content})
                delivered = websocket.receive_json()
                self.assertEqual(delivered["type"], "message")
                self.assertEqual(delivered["message"]["content"], content)

        alice_chats = self.client.get("/users", headers=self.auth("alice-token")).json()
        bob_chats = self.client.get("/users", headers=self.auth("bob-token")).json()
        self.assertEqual([chat["id"] for chat in alice_chats], [self.bob_id])
        self.assertEqual(
            {chat["id"] for chat in bob_chats},
            {self.alice_id, self.carol_id},
        )

    def test_message_history_is_scoped_to_the_authenticated_participant_pair(self):
        response = self.client.get(
            f"/messages/{self.bob_id}",
            headers=self.auth("alice-token"),
        )
        self.assertEqual(response.json(), [])

        with Session(self.engine) as db:
            chat_service.save_message(db, self.alice_id, self.bob_id, "Alice and Bob")

        response = self.client.get(
            f"/messages/{self.bob_id}",
            headers=self.auth("alice-token"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([message["content"] for message in response.json()], ["Alice and Bob"])
        self.assertNotIn(
            "Bob and Carol private message",
            [message["content"] for message in response.json()],
        )

    def test_nonparticipants_cannot_download_attachments(self):
        response = self.client.get(
            f"/attachments/{self.attachment_id}",
            headers=self.auth("alice-token"),
        )
        self.assertEqual(response.status_code, 403)

    def test_existing_username_cannot_be_claimed_without_its_session_token(self):
        response = self.client.post("/users", json={"username": "Alice"})
        self.assertEqual(response.status_code, 409)

        response = self.client.post(
            "/users",
            json={"username": "Alice", "token": "wrong-token"},
        )
        self.assertEqual(response.status_code, 409)

        response = self.client.post(
            "/users",
            json={"username": "Alice", "token": "alice-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["token"], "alice-token")

    def test_private_endpoints_require_authentication(self):
        self.assertEqual(self.client.get("/users").status_code, 401)
        self.assertEqual(self.client.get("/users/search?q=Bob").status_code, 401)
        self.assertEqual(self.client.get(f"/messages/{self.bob_id}").status_code, 401)
        self.assertEqual(self.client.get(f"/attachments/{self.attachment_id}").status_code, 401)


if __name__ == "__main__":
    unittest.main()
