"""
ReconcileAI - Database Engine & Session Management
Provides SQLAlchemy session lifecycle and database initialization.
Supports SQLite out of the box with zero configuration and seamless PostgreSQL migration.
"""

import os
import sys

# Ensure project root is in sys.path when executed directly
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from backend.config import settings

# For SQLite, check_same_thread=False allows FastAPI multithreaded request handling
connect_args = {"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    echo=False  # Set to True if SQL query debugging is needed
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """
    FastAPI dependency that provides a transactional database session per request.
    Automatically closes the session when the request completes.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """
    Initializes all database tables registered in SQLAlchemy models.
    Safe to call multiple times (creates tables only if they do not exist).
    """
    import backend.models  # Ensures all model classes are imported & registered
    Base.metadata.create_all(bind=engine)
    print("[Database] Schema initialized successfully. Tables created.")

if __name__ == "__main__":
    init_db()
