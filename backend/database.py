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

from sqlalchemy import create_engine, inspect
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

def _migrate_sqlite_schema(target_engine=None):
    """
    Safely and idempotently adds missing Phase 12A SLA columns to existing SQLite tables.
    Works around SQLite ALTER TABLE restrictions (no non-constant defaults on ADD COLUMN)
    and backfills SLA defaults for existing rows without altering historical data.
    """
    db_engine = target_engine or engine
    inspector = inspect(db_engine)
    table_names = inspector.get_table_names()
    if "reconciliation_exceptions" not in table_names:
        return

    existing_columns = {col["name"] for col in inspector.get_columns("reconciliation_exceptions")}

    with db_engine.begin() as conn:
        # 1. sla_duration_hours: Float, NOT NULL DEFAULT 24.0
        if "sla_duration_hours" not in existing_columns:
            conn.exec_driver_sql(
                "ALTER TABLE reconciliation_exceptions ADD COLUMN sla_duration_hours FLOAT NOT NULL DEFAULT 24.0;"
            )

        # 2. sla_status: VARCHAR(20), NOT NULL DEFAULT 'OK'
        if "sla_status" not in existing_columns:
            conn.exec_driver_sql(
                "ALTER TABLE reconciliation_exceptions ADD COLUMN sla_status VARCHAR(20) NOT NULL DEFAULT 'OK';"
            )

        # 3. escalation_level: INTEGER, NOT NULL DEFAULT 0
        if "escalation_level" not in existing_columns:
            conn.exec_driver_sql(
                "ALTER TABLE reconciliation_exceptions ADD COLUMN escalation_level INTEGER NOT NULL DEFAULT 0;"
            )

        # 4. escalated_at: DATETIME, DEFAULT NULL
        if "escalated_at" not in existing_columns:
            conn.exec_driver_sql(
                "ALTER TABLE reconciliation_exceptions ADD COLUMN escalated_at DATETIME DEFAULT NULL;"
            )

        # 5. sla_deadline: DATETIME
        # SQLite disallows non-constant defaults like CURRENT_TIMESTAMP in ADD COLUMN.
        # Add column safely with DEFAULT NULL, then backfill created_at + sla_duration_hours.
        if "sla_deadline" not in existing_columns:
            conn.exec_driver_sql(
                "ALTER TABLE reconciliation_exceptions ADD COLUMN sla_deadline DATETIME DEFAULT NULL;"
            )
            conn.exec_driver_sql(
                """
                UPDATE reconciliation_exceptions 
                SET sla_deadline = datetime(
                    COALESCE(created_at, datetime('now')), 
                    '+' || CAST(COALESCE(sla_duration_hours, 24.0) AS INT) || ' hours'
                ) 
                WHERE sla_deadline IS NULL;
                """
            )

        # Ensure performance indexes exist on new columns
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_exc_sla_deadline ON reconciliation_exceptions (sla_deadline);")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_exc_sla_status ON reconciliation_exceptions (sla_status);")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_exc_escalation_level ON reconciliation_exceptions (escalation_level);")

def init_db():
    """
    Initializes all database tables registered in SQLAlchemy models.
    Safe to call multiple times (creates tables only if they do not exist).
    Also performs safe, idempotent schema upgrades for SQLite.
    """
    import backend.models  # Ensures all model classes are imported & registered
    Base.metadata.create_all(bind=engine)
    if "sqlite" in str(engine.url):
        _migrate_sqlite_schema(engine)
    print("[Database] Schema initialized successfully. Tables created.")

if __name__ == "__main__":
    init_db()
