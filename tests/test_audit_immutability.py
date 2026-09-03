"""
ReconcileAI - AuditLog Immutability Test Suite (Step 4B-2)
Verifies that AuditLog records are append-only and strictly immutable:
1. ORM instance updates are rejected.
2. Bulk query updates (Query.update) are rejected.
3. 2.0-style statement updates (session.execute(update)) are rejected.
4. ORM instance deletes (session.delete) are rejected outside cleanup context.
5. Bulk query deletes (Query.delete) are rejected outside cleanup context.
6. 2.0-style statement deletes (session.execute(delete)) are rejected outside cleanup context.
7. Core audit row data is preserved completely unchanged after rejected mutations.
8. Core audit row survives after rejected deletion attempts.
9. Insert and Select operations continue functioning normally.
10. AuditService operations remain fully functional.
11. Explicit audit_log_cleanup_context enables cleanup while update remains strictly forbidden.
"""

from __future__ import annotations

import uuid
import pytest
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from backend.database import SessionLocal, init_db
from backend.models.audit import (
    AuditLog,
    AuditLogImmutableError,
    audit_log_cleanup_context,
)
from backend.services.audit_service import AuditService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def setup_database():
    """Ensures database schema exists and cleans up any test records."""
    init_db()
    with SessionLocal() as db:
        with audit_log_cleanup_context():
            db.query(AuditLog).filter(AuditLog.entity_id.like("TEST_IMMUT_%")).delete(synchronize_session=False)
            db.commit()
    yield
    with SessionLocal() as db:
        with audit_log_cleanup_context():
            db.query(AuditLog).filter(AuditLog.entity_id.like("TEST_IMMUT_%")).delete(synchronize_session=False)
            db.commit()


@pytest.fixture
def db_session():
    """Provides a fresh transactional session with teardown cleanup."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        with audit_log_cleanup_context():
            db.query(AuditLog).filter(AuditLog.entity_id.like("TEST_IMMUT_%")).delete(synchronize_session=False)
            db.commit()
        db.close()


def create_sample_audit_log(db: Session, suffix: str = "001") -> AuditLog:
    """Helper to insert a committed sample AuditLog entry."""
    entry = AuditLog(
        audit_id=f"AUD_TEST_IMMUT_{suffix}_{uuid.uuid4().hex[:6].upper()}",
        actor="SYSTEM",
        action="TRANSACTION_INGESTED",
        entity="TRANSACTION",
        entity_id=f"TEST_IMMUT_{suffix}",
        old_value="INIT",
        new_value="COMPLETED",
        reason="Initial ingestion event.",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


# ---------------------------------------------------------------------------
# 1. ORM Instance Update Rejected
# ---------------------------------------------------------------------------

def test_orm_instance_update_rejected(db_session: Session):
    """Modifying an ORM instance attribute and flushing must raise AuditLogImmutableError."""
    entry = create_sample_audit_log(db_session, "UP_INST")

    entry.actor = "MALICIOUS_ACTOR"
    entry.reason = "Tampered reason."

    with pytest.raises(AuditLogImmutableError, match="strictly immutable and cannot be updated"):
        db_session.flush()

    db_session.rollback()


# ---------------------------------------------------------------------------
# 2. Bulk Query Update Rejected
# ---------------------------------------------------------------------------

def test_bulk_query_update_rejected(db_session: Session):
    """Query.update() targeting AuditLog must raise AuditLogImmutableError before execution."""
    entry = create_sample_audit_log(db_session, "UP_BULK")

    with pytest.raises(AuditLogImmutableError, match="Bulk UPDATE operations on AuditLog are strictly forbidden"):
        db_session.query(AuditLog).filter_by(audit_id=entry.audit_id).update({"actor": "FORGED_ACTOR"})

    db_session.rollback()


# ---------------------------------------------------------------------------
# 3. 2.0 Statement-Level Update Rejected
# ---------------------------------------------------------------------------

def test_execute_update_statement_rejected(db_session: Session):
    """session.execute(update(AuditLog)) must raise AuditLogImmutableError before execution."""
    entry = create_sample_audit_log(db_session, "UP_STMT")

    stmt = update(AuditLog).where(AuditLog.audit_id == entry.audit_id).values(actor="STATEMENT_TAMPER")
    with pytest.raises(AuditLogImmutableError, match="Bulk UPDATE operations on AuditLog are strictly forbidden"):
        db_session.execute(stmt)

    db_session.rollback()


# ---------------------------------------------------------------------------
# 4. ORM Instance Delete Rejected
# ---------------------------------------------------------------------------

def test_orm_instance_delete_rejected(db_session: Session):
    """Calling session.delete(instance) outside cleanup context must raise AuditLogImmutableError."""
    entry = create_sample_audit_log(db_session, "DEL_INST")

    db_session.delete(entry)
    with pytest.raises(AuditLogImmutableError, match="strictly immutable and cannot be deleted"):
        db_session.flush()

    db_session.rollback()


# ---------------------------------------------------------------------------
# 5. Bulk Query Delete Rejected
# ---------------------------------------------------------------------------

def test_bulk_query_delete_rejected(db_session: Session):
    """Query.delete() targeting AuditLog outside cleanup context must raise AuditLogImmutableError."""
    entry = create_sample_audit_log(db_session, "DEL_BULK")

    with pytest.raises(AuditLogImmutableError, match="Bulk DELETE operations on AuditLog are strictly forbidden"):
        db_session.query(AuditLog).filter_by(audit_id=entry.audit_id).delete()

    db_session.rollback()


# ---------------------------------------------------------------------------
# 6. 2.0 Statement-Level Delete Rejected
# ---------------------------------------------------------------------------

def test_execute_delete_statement_rejected(db_session: Session):
    """session.execute(delete(AuditLog)) outside cleanup context must raise AuditLogImmutableError."""
    entry = create_sample_audit_log(db_session, "DEL_STMT")

    stmt = delete(AuditLog).where(AuditLog.audit_id == entry.audit_id)
    with pytest.raises(AuditLogImmutableError, match="Bulk DELETE operations on AuditLog are strictly forbidden"):
        db_session.execute(stmt)

    db_session.rollback()


# ---------------------------------------------------------------------------
# 7. Core Audit Row Unchanged After Rejected Update
# ---------------------------------------------------------------------------

def test_core_audit_row_unchanged_after_rejected_update(db_session: Session):
    """Verifies that original database record remains 100% unaltered after rejected update."""
    entry = create_sample_audit_log(db_session, "ROLLBACK_UP")
    audit_id = entry.audit_id

    entry.actor = "TAMPERED"
    entry.new_value = "ILLEGAL_MUTATION"
    with pytest.raises(AuditLogImmutableError):
        db_session.commit()

    db_session.rollback()

    # Re-query in a fresh query state
    refetched = db_session.query(AuditLog).filter_by(audit_id=audit_id).first()
    assert refetched is not None
    assert refetched.actor == "SYSTEM"
    assert refetched.new_value == "COMPLETED"
    assert refetched.reason == "Initial ingestion event."


# ---------------------------------------------------------------------------
# 8. Core Audit Row Survives Rejected Delete
# ---------------------------------------------------------------------------

def test_core_audit_row_survives_rejected_delete(db_session: Session):
    """Verifies that record still exists and is untouched after rejected delete attempts."""
    entry = create_sample_audit_log(db_session, "ROLLBACK_DEL")
    audit_id = entry.audit_id

    # Try instance deletion
    db_session.delete(entry)
    with pytest.raises(AuditLogImmutableError):
        db_session.commit()
    db_session.rollback()

    # Try bulk query deletion
    with pytest.raises(AuditLogImmutableError):
        db_session.query(AuditLog).filter_by(audit_id=audit_id).delete()
    db_session.rollback()

    # Verify presence
    refetched = db_session.query(AuditLog).filter_by(audit_id=audit_id).first()
    assert refetched is not None
    assert refetched.audit_id == audit_id
    assert refetched.entity_id == "TEST_IMMUT_ROLLBACK_DEL"


# ---------------------------------------------------------------------------
# 9. Insert and Select Still Work
# ---------------------------------------------------------------------------

def test_insert_and_select_still_work(db_session: Session):
    """Verifies that standard insert and query operations work seamlessly."""
    entry = AuditLog(
        audit_id=f"AUD_TEST_IMMUT_INSERT_{uuid.uuid4().hex[:6].upper()}",
        actor="HUMAN_OPERATOR",
        action="EXCEPTION_APPROVED",
        entity="EXCEPTION",
        entity_id="TEST_IMMUT_INSERT_001",
        reason="Approved by supervisor.",
    )
    db_session.add(entry)
    db_session.commit()
    db_session.refresh(entry)

    # SELECT via query
    found = db_session.query(AuditLog).filter_by(audit_id=entry.audit_id).first()
    assert found is not None
    assert found.actor == "HUMAN_OPERATOR"
    assert found.action == "EXCEPTION_APPROVED"

    # SELECT via filter
    all_found = db_session.query(AuditLog).filter(AuditLog.entity_id == "TEST_IMMUT_INSERT_001").all()
    assert len(all_found) == 1


# ---------------------------------------------------------------------------
# 10. AuditService Still Works
# ---------------------------------------------------------------------------

def test_audit_service_still_works(db_session: Session):
    """Verifies centralized AuditService integrates seamlessly with immutability rules."""
    service = AuditService(db=db_session)
    logged = service.log_action(
        actor="AI_CONTROLLER",
        action="AI_REASONED",
        entity="RECONCILIATION",
        entity_id="TEST_IMMUT_SVC_001",
        reason="Auto-investigation completed.",
        commit=True,
    )

    trail = service.get_audit_trail(entity_id="TEST_IMMUT_SVC_001")
    assert len(trail) == 1
    assert trail[0].audit_id == logged.audit_id
    assert trail[0].actor == "AI_CONTROLLER"


# ---------------------------------------------------------------------------
# 11. Cleanup Context Permits Deletion But Rejects Update
# ---------------------------------------------------------------------------

def test_cleanup_context_permits_deletion_but_blocks_update(db_session: Session):
    """
    Verifies that inside audit_log_cleanup_context:
    - deletion is permitted
    - update is STILL strictly forbidden
    """
    entry = create_sample_audit_log(db_session, "CLEANUP_CTX")
    audit_id = entry.audit_id

    # Update must STILL fail even in cleanup context
    with audit_log_cleanup_context():
        with pytest.raises(AuditLogImmutableError, match="Bulk UPDATE operations on AuditLog are strictly forbidden"):
            db_session.query(AuditLog).filter_by(audit_id=audit_id).update({"actor": "FORGED"})
        db_session.rollback()

    # Deletion must succeed in cleanup context
    with audit_log_cleanup_context():
        deleted_count = db_session.query(AuditLog).filter_by(audit_id=audit_id).delete()
        db_session.commit()
        assert deleted_count == 1

    # Verify row is removed
    assert db_session.query(AuditLog).filter_by(audit_id=audit_id).first() is None
