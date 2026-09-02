"""
Phase 12A Unit Tests: Model & Database Foundation
Verifies:
1. ReconciliationException SLA fields exist and default correctly.
2. ReconciliationException accepts custom values for SLA and escalation fields.
3. NotificationLog can be created and queried with all required fields.
4. NotificationLog idempotency_key database uniqueness constraint is enforced.
5. Database initialization (init_db) is idempotent and safe to call repeatedly.
6. SQLite schema migration (_migrate_sqlite_schema) safely adds missing columns
   and backfills existing rows without modifying existing historical data.
"""

import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine, inspect, Column, Integer, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.exc import IntegrityError

from backend.database import SessionLocal, init_db, _migrate_sqlite_schema
from backend.models import ReconciliationException, NotificationLog

@pytest.fixture(autouse=True)
def clean_test_data():
    """Ensures database is initialized and cleans test records before and after each test."""
    init_db()
    db: Session = SessionLocal()
    try:
        db.query(NotificationLog).filter(NotificationLog.notification_id.like("%PHASE12A%")).delete(synchronize_session=False)
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like("%PHASE12A%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(NotificationLog).filter(NotificationLog.notification_id.like("%PHASE12A%")).delete(synchronize_session=False)
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like("%PHASE12A%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()

def test_reconciliation_exception_sla_fields_defaults():
    """Verify that a newly created ReconciliationException has default SLA and escalation values."""
    db: Session = SessionLocal()
    try:
        exc = ReconciliationException(
            exception_id="EXC_PHASE12A_DEF_001",
            reconciliation_id="REC_PHASE12A_001",
            transaction_id="TXN_PHASE12A_001",
            category="AMOUNT_MISMATCH",
            severity="MEDIUM",
            difference_amount=120.0,
            status="OPEN"
        )
        db.add(exc)
        db.commit()
        db.refresh(exc)

        # Check default values
        assert exc.sla_duration_hours == 24.0
        assert exc.sla_deadline is not None
        assert isinstance(exc.sla_deadline, datetime)
        assert exc.sla_status == "OK"
        assert exc.escalation_level == 0
        assert exc.escalated_at is None
    finally:
        db.close()

def test_reconciliation_exception_sla_fields_custom():
    """Verify that explicit SLA and escalation values are persisted accurately."""
    db: Session = SessionLocal()
    try:
        custom_deadline = datetime.now(timezone.utc) + timedelta(hours=4)
        custom_escalated = datetime.now(timezone.utc)

        exc = ReconciliationException(
            exception_id="EXC_PHASE12A_CUSTOM_001",
            reconciliation_id="REC_PHASE12A_002",
            transaction_id="TXN_PHASE12A_002",
            category="MISSING_BANK_TRANSACTION",
            severity="HIGH",
            difference_amount=500.0,
            status="OPEN",
            sla_duration_hours=4.0,
            sla_deadline=custom_deadline,
            sla_status="WARNING",
            escalation_level=1,
            escalated_at=custom_escalated
        )
        db.add(exc)
        db.commit()
        db.refresh(exc)

        assert exc.sla_duration_hours == 4.0
        assert exc.sla_status == "WARNING"
        assert exc.escalation_level == 1
        assert exc.escalated_at is not None
    finally:
        db.close()

def test_notification_log_creation():
    """Verify NotificationLog insertion, persistence, and field retrieval."""
    db: Session = SessionLocal()
    try:
        notif = NotificationLog(
            notification_id="NOTIF_PHASE12A_001",
            exception_id="EXC_PHASE12A_001",
            event_type="SLA_WARNING",
            recipient_role="PRIMARY_REVIEWER",
            recipient_email="reviewer@example.com",
            subject="[SLA WARNING] Exception EXC_PHASE12A_001",
            body="Exception approaching SLA limit.",
            idempotency_key="EXC_PHASE12A_001:SLA_WARNING:0",
            status="SENT"
        )
        db.add(notif)
        db.commit()
        db.refresh(notif)

        fetched = db.query(NotificationLog).filter_by(notification_id="NOTIF_PHASE12A_001").first()
        assert fetched is not None
        assert fetched.exception_id == "EXC_PHASE12A_001"
        assert fetched.event_type == "SLA_WARNING"
        assert fetched.recipient_role == "PRIMARY_REVIEWER"
        assert fetched.recipient_email == "reviewer@example.com"
        assert fetched.subject == "[SLA WARNING] Exception EXC_PHASE12A_001"
        assert fetched.idempotency_key == "EXC_PHASE12A_001:SLA_WARNING:0"
        assert fetched.status == "SENT"
        assert fetched.sent_at is not None
    finally:
        db.close()

def test_notification_log_idempotency_key_uniqueness():
    """Verify that duplicate idempotency_key raises IntegrityError at the database level."""
    db: Session = SessionLocal()
    try:
        notif1 = NotificationLog(
            notification_id="NOTIF_PHASE12A_DUP_001",
            exception_id="EXC_PHASE12A_DUP",
            event_type="SLA_BREACH",
            recipient_role="FINANCE_SUPERVISOR",
            recipient_email="supervisor@example.com",
            subject="[SLA BREACH] Exception EXC_PHASE12A_DUP",
            body="Breach notification 1",
            idempotency_key="EXC_PHASE12A_DUP:SLA_BREACH:1",
            status="SENT"
        )
        db.add(notif1)
        db.commit()

        # Attempt to insert identical idempotency_key with different notification_id
        notif2 = NotificationLog(
            notification_id="NOTIF_PHASE12A_DUP_002",
            exception_id="EXC_PHASE12A_DUP",
            event_type="SLA_BREACH",
            recipient_role="FINANCE_SUPERVISOR",
            recipient_email="supervisor@example.com",
            subject="[SLA BREACH] Duplicate attempt",
            body="Breach notification 2",
            idempotency_key="EXC_PHASE12A_DUP:SLA_BREACH:1",  # Same unique key
            status="SENT"
        )
        db.add(notif2)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()

def test_database_init_idempotency():
    """Verify that calling init_db multiple times does not raise errors or corrupt schema."""
    init_db()
    init_db()
    init_db()

def test_sqlite_migration_adds_missing_columns_to_existing_table(tmp_path):
    """
    Simulate an existing SQLite database from Phase 11 lacking the new Phase 12A columns,
    and verify that _migrate_sqlite_schema adds them safely and backfills values.
    """
    test_db_path = tmp_path / "legacy_test.db"
    legacy_engine = create_engine(f"sqlite:///{test_db_path}")

    TestBase = declarative_base()

    class LegacyException(TestBase):
        __tablename__ = "reconciliation_exceptions"
        id = Column(Integer, primary_key=True, autoincrement=True)
        exception_id = Column(String(100), unique=True, nullable=False)
        category = Column(String(50), nullable=False)
        status = Column(String(50), default="OPEN", nullable=False)
        created_at = Column(DateTime, default=lambda: datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc), nullable=False)

    TestBase.metadata.create_all(bind=legacy_engine)

    LegacySession = sessionmaker(bind=legacy_engine)
    session = LegacySession()
    session.add(LegacyException(
        exception_id="EXC_LEGACY_001",
        category="AMOUNT_MISMATCH",
        status="OPEN",
        created_at=datetime(2026, 9, 1, 12, 0, 0)
    ))
    session.commit()
    session.close()

    # Verify initially missing columns
    inspector_before = inspect(legacy_engine)
    cols_before = {c["name"] for c in inspector_before.get_columns("reconciliation_exceptions")}
    assert "sla_duration_hours" not in cols_before
    assert "sla_deadline" not in cols_before
    assert "sla_status" not in cols_before
    assert "escalation_level" not in cols_before
    assert "escalated_at" not in cols_before

    # Run the SQLite migration helper
    _migrate_sqlite_schema(legacy_engine)

    # Verify columns now exist
    inspector_after = inspect(legacy_engine)
    cols_after = {c["name"] for c in inspector_after.get_columns("reconciliation_exceptions")}
    assert "sla_duration_hours" in cols_after
    assert "sla_deadline" in cols_after
    assert "sla_status" in cols_after
    assert "escalation_level" in cols_after
    assert "escalated_at" in cols_after

    # Verify existing row has been backfilled with safe defaults
    with legacy_engine.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT exception_id, sla_duration_hours, sla_deadline, sla_status, escalation_level, escalated_at "
            "FROM reconciliation_exceptions WHERE exception_id = 'EXC_LEGACY_001';"
        ).fetchone()

        assert row is not None
        assert row[0] == "EXC_LEGACY_001"
        assert row[1] == 24.0
        assert row[2] is not None  # sla_deadline backfilled
        assert row[3] == "OK"      # sla_status default
        assert row[4] == 0         # escalation_level default
        assert row[5] is None      # escalated_at default
