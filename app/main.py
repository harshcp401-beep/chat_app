"""Entry point. Creates the app, tables, routes, static files and the home page."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import models  # noqa: F401  (importing registers the tables)
from .database import Base, engine
from .routes import attachments, messages, users, ws
from .services import attachment_service
from .services.chat_service import ChatError

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)  # create tables on startup if missing
    yield


app = FastAPI(title="Real-Time Chat", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

app.include_router(users.router)
app.include_router(messages.router)
app.include_router(attachments.router)
app.include_router(ws.router)


@app.exception_handler(ChatError)
async def chat_error_handler(request: Request, exc: ChatError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "image_max_bytes": attachment_service.IMAGE_MAX_BYTES,
            "file_max_bytes": attachment_service.FILE_MAX_BYTES,
        },
    )
