"""
Database connection setup.

Single SQLite file for the whole app — property data, conversation history,
and tool-call logs all live here so the business interface can query them
directly with no separate logging system.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base


# SQLite database
DATABASE_URL = "sqlite:///./portfolio.db"


# check_same_thread=False allows FastAPI to use SQLite across threads.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)


# Database session
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)


# Base class for SQLAlchemy models
Base = declarative_base()


def get_db():
    """
    FastAPI dependency.

    Creates a database session for the request and
    closes it after the request is finished.
    """
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    Create all database tables that are defined by the SQLAlchemy models.
    """
    Base.metadata.create_all(bind=engine)