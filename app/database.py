"""
Database connection setup.

Single SQLite file for the whole app — property data, conversation history,
and tool-call logs all live here so the business interface can query them
directly with no separate logging system.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = "sqlite:///./portfolio.db"

# check_same_thread=False: FastAPI can use the same connection pool across
# async request handlers; we're not sharing a single connection across threads.
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a session, always closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
