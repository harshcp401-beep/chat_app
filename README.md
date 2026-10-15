# Real-Time One-to-One Chat (FastAPI + WebSocket)

A beginner-friendly chat app. Two people open it in two browsers, pick different usernames,
select each other, and chat instantly. Messages are saved in SQLite.

## 1. Run it (Windows PowerShell)

```powershell
cd chat_app
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

If activation is blocked: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, then activate again.
Open http://localhost:8000. Stop the server with Ctrl+C. API docs: http://localhost:8000/docs

## 2. Test with two users

1. Normal browser window -> http://localhost:8000 -> join as `User A`.
2. Incognito window -> http://localhost:8000 -> join as `User B`.
3. Each sees the other in the sidebar with a green "Online" dot. Click it.
4. User B sends "Hi" -> appears instantly for User A (with an unread badge if A has not opened B). Unread counts survive refreshes and are cleared when the conversation is opened.
5. User A replies "Hello" -> appears instantly for User B.
6. Refresh a window: you stay signed in and your history reloads from SQLite.
7. Stop and restart the server: messages are still there (they live in `chat.db`).
8. Try joining a 2nd tab as `User A` while A is online -> "username currently in use".

(A normal window and an incognito window have separate storage, so they behave like two different people.)

## 3. Architecture

```
Browser A (chat.js) --HTTP (join, user list, history)--+
Browser A (chat.js) --WebSocket /ws/1?token=...--------+--> FastAPI + Uvicorn --> SQLAlchemy --> chat.db (SQLite)
Browser B (chat.js) --HTTP--------------------------- +          |
Browser B (chat.js) --WebSocket /ws/2?token=...--------+   ConnectionManager (who is online?)
```

- **HTTP (REST)** is for one-off requests: join, list users, load history.
- **WebSocket** is one long-lived two-way connection per browser, so the server can PUSH a message the instant it arrives.
- **ConnectionManager** is a dict `{user_id: websocket}` in memory. It answers "is X online?" and "send this to X".
- **SQLite** stores users, messages, and each conversation's last-read position permanently.

Message flow: B's browser sends `{"receiver_id": 1, "content": "Hi"}` -> server checks the token to know the sender is B,
validates the data, saves it, sends it ONLY to socket #1 (A) and echoes it back to socket #2 (B). Nobody else receives it.

### Compared with Node.js + Socket.IO
| Node + Socket.IO | This project |
|---|---|
| `io.on("connection", socket => ...)` | `@router.websocket("/ws/{user_id}")` |
| `socket.on("message", ...)` | `await websocket.receive_text()` in a `while True` loop |
| `io.to(socketId).emit(...)` | `manager.send_to_user(user_id, payload)` |
| Rooms / auto-reconnect built in | We keep our own dict and write reconnect logic in `chat.js` |
| Socket.IO protocol on top of WS | Plain standard WebSocket (no extra library in the browser) |

## 4. File responsibilities

| File | Job |
|---|---|
| `app/main.py` | Creates the FastAPI app, creates tables, mounts static files, registers routes, serves the home page |
| `app/database.py` | DB engine, session factory, `get_db` dependency (reads `.env`) |
| `app/models.py` | `User`, `Message`, `Attachment`, and conversation-read tables |
| `app/schemas.py` | Pydantic models that validate input (username rules, message rules) and shape output |
| `app/websocket_manager.py` | Tracks online users; sends to one user or broadcasts presence |
| `app/services/chat_service.py` | Business rules: join/duplicate handling, no self-chat, save and load messages |
| `app/services/attachment_service.py` | File validation, private storage, upload limits, and attachment authorization |
| `app/routes/users.py` | `POST /users`, `GET /users`, `GET /users/online` |
| `app/routes/messages.py` | `GET /messages/{other_user_id}` (loads history and marks it read), `POST /messages/{other_user_id}/read` |
| `app/routes/attachments.py` | Authenticated attachment upload and download endpoints |
| `app/routes/ws.py` | `WS /ws/{user_id}` real-time endpoint (added; keeps `main.py` small) |
| `app/deps.py` | `get_current_user`: reads the `Authorization: Bearer <token>` header (added; shared by routes) |
| `app/templates/index.html` | The page: login screen, sidebar, chat area |
| `app/static/css/style.css` | Styling, bubbles, responsive layout |
| `app/static/js/chat.js` | Login, WebSocket, rendering, reconnect logic |

Differences from the suggested structure: added `__init__.py` files (make folders Python packages),
`routes/ws.py` and `deps.py` (to keep each file focused on one job).

## 5. Emoji and attachments

The composer supports emoji insertion, images, and documents. The emoji picker uses
`emoji-picker-element` from jsDelivr, so a browser needs internet access to load that
component. Images (`.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`) default to a 5 MiB limit.
Documents (`.pdf`, `.txt`, `.doc`, `.docx`, `.xls`, `.xlsx`, `.csv`) default to a 10 MiB
limit. Configure these with positive integer byte counts in the server environment:

```text
CHAT_IMAGE_MAX_BYTES=5242880
CHAT_FILE_MAX_BYTES=10485760
CHAT_UPLOAD_DIR=C:\path\outside\the\public\web\root
```

`CHAT_UPLOAD_DIR` defaults to `.chat_uploads` beside `app/`; the application rejects a
configured path inside the public `app/static` directory. Files receive generated
storage names and are served only through `GET /attachments/{attachment_id}` after
authentication and a conversation-participant check. The original display name and
server-detected media type are metadata only; uploads are never executed or served
directly from the static directory.

New databases create the `attachments` table automatically at startup. Existing
databases are also upgraded additively by SQLAlchemy `create_all`; existing message
rows and columns are unchanged, so no manual migration is required. Attachment messages
use the existing `messages` table for conversation, unread, and read-receipt behavior;
the `attachments` table links file metadata to the message.

The API additions are:

- `POST /attachments/{other_user_id}` — authenticated multipart upload (`file`, optional
  `caption`), persists the attachment and its message, and notifies the receiver over
  the existing WebSocket.
- `GET /attachments/{attachment_id}` — authenticated download available only to that
  attachment's sender and receiver.
- `GET /messages/{other_user_id}` and WebSocket `message` events now include `kind` and
  nullable attachment metadata; existing text messages remain `kind: "text"`.

## 6. Key code in simple English

```python
await websocket.accept()                       # say "yes" to the browser's connection request
user = chat_service.authenticate_ws(db, user_id, token)
if user is None: await websocket.close(code=4401)   # wrong token -> reject
await manager.connect(user_id, websocket)      # remember this socket in the online dict
while True:                                    # keep listening until the browser disconnects
    raw = await websocket.receive_text()       # wait here (without blocking others) for a message
    incoming = IncomingMessage.model_validate(json.loads(raw))   # Pydantic checks the data
    saved = chat_service.save_message(db, user_id, incoming.receiver_id, incoming.content)
    await manager.send_to_user(incoming.receiver_id, payload)    # to the receiver only
    await websocket.send_json(payload)                           # echo to the sender
```
`user_id` for the sender always comes from the verified connection, never from the message body,
so nobody can send "as" someone else.

## 7. Security limits of this prototype (read before real use)

- **Username = identity.** There is no password. When a username is offline, anyone who types it can claim it.
- The session token is a random secret kept in the browser's `sessionStorage` and stored in plain text in the DB.
- No rate limiting, no HTTPS, no message encryption; the WebSocket accepts any origin.
- Online state lives in server memory, so it works for ONE server process only (no `--workers 2`).

**Before production:**
1. Real accounts: email + password hashed with Argon2/bcrypt, or OAuth (Google, etc.).
2. Login returns a short-lived JWT (or secure HttpOnly session cookie); validate it on HTTP and WebSocket; hash stored tokens.
3. Authorization checks on every request (already done here: you can only read your own conversations).
4. HTTPS/WSS, check the `Origin` header, rate-limit messages, add CSRF protection if you use cookies.
5. PostgreSQL + Alembic migrations, and Redis pub/sub so multiple server processes can deliver messages to each other.
6. Pagination of history, delivery/read receipts, and tests (pytest).

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| `python` not found | Install Python 3.10+ from python.org and tick "Add to PATH"; try `py -m venv .venv` |
| Cannot run `Activate.ps1` | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| `No module named app` | Run uvicorn from inside the `chat_app` folder (the one containing `app/`) |
| Port 8000 in use | Use `--port 8001` and open that URL |
| Red "Connecting... / Connection lost" banner | Server stopped or crashed; check the PowerShell window. It reconnects automatically |
| "username currently in use" | Another window is signed in with that name; pick another or close it |
| "opened in another window" banner | The same account connected elsewhere (e.g. a duplicated tab); close one |
| Want a clean slate | Stop the server, delete `chat.db`, start again |
| Page looks unstyled / old JS | Hard refresh with Ctrl+Shift+R |
