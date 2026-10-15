"""Database setup: the engine (connection), sessions, and the Base class for models."""
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv()  # reads the .env file in the project root

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./chat.db")

# SQLite only allows one thread by default; FastAPI uses several, so we relax that.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """All database models inherit from this."""


def get_db():
    """FastAPI dependency: gives each request its own DB session, then closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
