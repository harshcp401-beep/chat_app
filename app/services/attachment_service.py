"""Validate and persist files shared in conversations."""
import os
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import UploadFile
from sqlalchemy.orm import Session

from ..models import Attachment, Message
from . import chat_service
from .chat_service import ChatError


def _positive_limit(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw is not None else default
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer.") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer.")
    return value


IMAGE_MAX_BYTES = _positive_limit("CHAT_IMAGE_MAX_BYTES", 5 * 1024 * 1024)
FILE_MAX_BYTES = _positive_limit("CHAT_FILE_MAX_BYTES", 10 * 1024 * 1024)
UPLOAD_DIR = Path(
    os.getenv(
        "CHAT_UPLOAD_DIR",
        str(Path(__file__).resolve().parents[2] / ".chat_uploads"),
    )
).expanduser().resolve()
STATIC_DIR = (Path(__file__).resolve().parents[1] / "static").resolve()

if UPLOAD_DIR == STATIC_DIR or STATIC_DIR in UPLOAD_DIR.parents:
    raise RuntimeError("CHAT_UPLOAD_DIR must be outside the public static directory.")

IMAGE_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}
DOCUMENT_TYPES = {
    "pdf": "application/pdf",
    "txt": "text/plain",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
}
CHUNK_SIZE = 64 * 1024


def _display_name(filename: str | None) -> tuple[str, str]:
    if not filename:
        raise ChatError("Choose a file to upload.")
    name = filename.strip()
    if (
        not name
        or len(name) > 255
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
    ):
        raise ChatError("The file name is invalid.")
    if "." not in name:
        raise ChatError("This file type is not supported.", 415)
    extension = name.rsplit(".", 1)[1].lower()
    if extension not in IMAGE_TYPES and extension not in DOCUMENT_TYPES:
        raise ChatError("This file type is not supported.", 415)
    return name, extension


def _validate_file(path: Path, extension: str) -> None:
    with path.open("rb") as source:
        header = source.read(16)

    valid = False
    if extension in {"jpg", "jpeg"}:
        valid = header.startswith(b"\xff\xd8\xff")
    elif extension == "png":
        valid = header.startswith(b"\x89PNG\r\n\x1a\n")
    elif extension == "gif":
        valid = header.startswith((b"GIF87a", b"GIF89a"))
    elif extension == "webp":
        valid = len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    elif extension == "pdf":
        valid = header.startswith(b"%PDF-")
    elif extension in {"doc", "xls"}:
        valid = header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    elif extension in {"docx", "xlsx"}:
        valid = zipfile.is_zipfile(path)
        if valid:
            try:
                with zipfile.ZipFile(path) as archive:
                    names = archive.namelist()
                    main_document = "word/document.xml" if extension == "docx" else "xl/workbook.xml"
                    valid = main_document in names and all(
                        not PurePosixPath(name).is_absolute()
                        and ".." not in PurePosixPath(name).parts
                        and "\\" not in name
                        for name in names
                    )
            except zipfile.BadZipFile:
                valid = False
    elif extension in {"txt", "csv"}:
        content = path.read_bytes()
        valid = b"\x00" not in content
        if valid:
            try:
                content.decode("utf-8-sig")
            except UnicodeDecodeError:
                valid = False

    if not valid:
        raise ChatError("The file contents do not match the selected file type.", 415)


async def save_attachment_message(
    db: Session,
    sender_id: int,
    receiver_id: int,
    upload: UploadFile,
    caption: str,
) -> Message:
    chat_service.check_other_user(db, sender_id, receiver_id)
    caption = caption.strip()
    if len(caption) > 2000:
        raise ChatError("Message is too long (max 2000 characters)")

    display_name, extension = _display_name(upload.filename)
    kind = "image" if extension in IMAGE_TYPES else "file"
    media_type = (IMAGE_TYPES if kind == "image" else DOCUMENT_TYPES)[extension]
    max_bytes = IMAGE_MAX_BYTES if kind == "image" else FILE_MAX_BYTES
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    storage_id = uuid.uuid4().hex
    storage_name = f"{storage_id}.{extension}"
    temporary_path = UPLOAD_DIR / f".{storage_id}.part"
    stored_path = UPLOAD_DIR / storage_name
    size = 0

    try:
        with temporary_path.open("wb") as destination:
            while chunk := await upload.read(CHUNK_SIZE):
                size += len(chunk)
                if size > max_bytes:
                    raise ChatError(
                        f"File exceeds the configured upload limit of {max_bytes} bytes.",
                        413,
                    )
                destination.write(chunk)

        if size == 0:
            raise ChatError("The selected file is empty.")
        _validate_file(temporary_path, extension)
        os.replace(temporary_path, stored_path)

        message = Message(
            sender_id=sender_id,
            receiver_id=receiver_id,
            content=caption,
        )
        attachment = Attachment(
            message=message,
            uploader_id=sender_id,
            receiver_id=receiver_id,
            kind=kind,
            display_name=display_name,
            storage_name=storage_name,
            media_type=media_type,
            size=size,
        )
        db.add_all([message, attachment])
        db.commit()
        db.refresh(message)
        return message
    except Exception:
        db.rollback()
        temporary_path.unlink(missing_ok=True)
        stored_path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


def get_authorized_attachment(db: Session, user_id: int, attachment_id: int) -> tuple[Attachment, Path]:
    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        raise ChatError("Attachment not found.", 404)
    if user_id not in {attachment.uploader_id, attachment.receiver_id}:
        raise ChatError("You are not allowed to access this attachment.", 403)

    path = (UPLOAD_DIR / attachment.storage_name).resolve()
    if path.parent != UPLOAD_DIR or not path.is_file():
        raise ChatError("Attachment not found.", 404)
    return attachment, path
