"""
ReconcileAI - AuditService Test Suite (Step 4B-1)
Verifies centralized AuditService creation, field persistence, transaction handling,
filtering, ordering, and unique ID generation.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy.orm import Session

from backend.database import SessionLocal, init_db
from backend.models.audit import AuditLog
from backend.services.audit_service import AuditService


# ---------------------------------------------------------------------------
# Test Fixtures & Isolation
# ---------------------------------------------------------------------------

def cleanup_audit_service_test_records(db: Session) -> None:
    """Safely cleans up any AuditLog records created during AuditService tests."""
    db.query(AuditLog).filter(
        (AuditLog.audit_id.like("AUD_TEST_AS_%")) |
        (AuditLog.entity_id.like("TEST_AS_%"))
    ).delete(synchronize_session=False)
    db.commit()


@pytest.fixture(scope="module", autouse=True)
def setup_audit_test_db():
    """Initializes schema and cleans up test records before and after test module."""
    init_db()
    db: Session = SessionLocal()
    try:
        cleanup_audit_service_test_records(db)
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        cleanup_audit_service_test_records(db)
    finally:
        db.close()


@pytest.fixture
def db_session():
    """Provides an isolated transactional database session with cleanup."""
    db: Session = SessionLocal()
    try:
        cleanup_audit_service_test_records(db)
        yield db
    finally:
        cleanup_audit_service_test_records(db)
        db.close()


# ---------------------------------------------------------------------------
# 1. log_action creates an AuditLog instance
# ---------------------------------------------------------------------------

def test_1_log_action_creates_audit_log(db_session: Session):
    """Verify log_action constructs and adds an AuditLog instance to the session."""
    service = AuditService(db=db_session)
    entry = service.log_action(
        actor="SYSTEM",
        action="TRANSACTION_INGESTED",
        entity="TRANSACTION",
        entity_id="TEST_AS_TXN_001",
    )
    assert isinstance(entry, AuditLog)
    assert entry.actor == "SYSTEM"
    assert entry.action == "TRANSACTION_INGESTED"
    assert entry.entity == "TRANSACTION"
    assert entry.entity_id == "TEST_AS_TXN_001"
    assert entry in db_session.new


# ---------------------------------------------------------------------------
# 2. All supplied fields are persisted correctly
# ---------------------------------------------------------------------------

def test_2_all_supplied_fields_persisted_correctly(db_session: Session):
    """Verify all optional and required audit fields are stored and retrieved intact."""
    service = AuditService(db=db_session)
    entry = service.log_action(
        actor="AI_CONTROLLER",
        action="AI_REASONED",
        entity="RECONCILIATION",
        entity_id="TEST_AS_REC_002",
        old_value="UNRESOLVED",
        new_value="REVIEW",
        reason="Moderate discrepancy requiring human operator oversight.",
        commit=True,
    )

    retrieved = db_session.query(AuditLog).filter_by(audit_id=entry.audit_id).first()
    assert retrieved is not None
    assert retrieved.actor == "AI_CONTROLLER"
    assert retrieved.action == "AI_REASONED"
    assert retrieved.entity == "RECONCILIATION"
    assert retrieved.entity_id == "TEST_AS_REC_002"
    assert retrieved.old_value == "UNRESOLVED"
    assert retrieved.new_value == "REVIEW"
    assert retrieved.reason == "Moderate discrepancy requiring human operator oversight."
    assert isinstance(retrieved.timestamp, datetime)


# ---------------------------------------------------------------------------
# 3. commit=False does not commit internally
# ---------------------------------------------------------------------------

def test_3_commit_false_does_not_commit(db_session: Session):
    """Verify commit=False adds to session but does not trigger session.commit()."""
    service = AuditService(db=db_session)
    with patch.object(db_session, "commit") as mock_commit:
        entry = service.log_action(
            actor="SYSTEM",
            action="AUTO_RECONCILED",
            entity="RECONCILIATION",
            entity_id="TEST_AS_REC_003",
            commit=False,
        )
        mock_commit.assert_not_called()
    assert entry in db_session.new


# ---------------------------------------------------------------------------
# 4. commit=True commits
# ---------------------------------------------------------------------------

def test_4_commit_true_commits_and_persists(db_session: Session):
    """Verify commit=True commits to the database session."""
    service = AuditService(db=db_session)
    with patch.object(db_session, "commit", wraps=db_session.commit) as spy_commit:
        entry = service.log_action(
            actor="HUMAN_OPERATOR",
            action="EXCEPTION_APPROVED",
            entity="EXCEPTION",
            entity_id="TEST_AS_EXC_004",
            commit=True,
        )
        spy_commit.assert_called_once()

    # Query from a separate fresh session to ensure persistence
    fresh_session = SessionLocal()
    try:
        persisted = fresh_session.query(AuditLog).filter_by(audit_id=entry.audit_id).first()
        assert persisted is not None
        assert persisted.entity_id == "TEST_AS_EXC_004"
    finally:
        fresh_session.close()


# ---------------------------------------------------------------------------
# 5. get_audit_trail returns chronological records
# ---------------------------------------------------------------------------

def test_5_get_audit_trail_returns_chronological_records(db_session: Session):
    """Verify get_audit_trail returns records ordered chronologically ascending."""
    service = AuditService(db=db_session)

    # Insert entries with distinct timestamps
    t1 = datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 8, 20, 11, 0, 0, tzinfo=timezone.utc)
    t3 = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)

    e2 = service.log_action(actor="SYSTEM", action="STEP_2", entity="ORDER", entity_id="TEST_AS_CHRONO_1", commit=False)
    e2.timestamp = t2
    e1 = service.log_action(actor="SYSTEM", action="STEP_1", entity="ORDER", entity_id="TEST_AS_CHRONO_1", commit=False)
    e1.timestamp = t1
    e3 = service.log_action(actor="SYSTEM", action="STEP_3", entity="ORDER", entity_id="TEST_AS_CHRONO_1", commit=False)
    e3.timestamp = t3

    db_session.commit()

    trail = service.get_audit_trail(entity_id="TEST_AS_CHRONO_1")
    assert len(trail) == 3
    assert [x.action for x in trail] == ["STEP_1", "STEP_2", "STEP_3"]


# ---------------------------------------------------------------------------
# 6. Filter by entity
# ---------------------------------------------------------------------------

def test_6_filter_by_entity(db_session: Session):
    """Verify get_audit_trail filters accurately by entity."""
    service = AuditService(db=db_session)
    service.log_action(actor="A", action="ACT_1", entity="WEBHOOK", entity_id="TEST_AS_ENT_1", commit=False)
    service.log_action(actor="B", action="ACT_2", entity="TRANSACTION", entity_id="TEST_AS_ENT_2", commit=False)
    db_session.commit()

    webhook_trail = service.get_audit_trail(entity="WEBHOOK")
    matching = [x for x in webhook_trail if x.entity_id in ("TEST_AS_ENT_1", "TEST_AS_ENT_2")]
    assert len(matching) == 1
    assert matching[0].entity_id == "TEST_AS_ENT_1"


# ---------------------------------------------------------------------------
# 7. Filter by entity_id
# ---------------------------------------------------------------------------

def test_7_filter_by_entity_id(db_session: Session):
    """Verify get_audit_trail filters accurately by entity_id."""
    service = AuditService(db=db_session)
    service.log_action(actor="A", action="CREATE", entity="REC", entity_id="TEST_AS_EID_A", commit=False)
    service.log_action(actor="B", action="UPDATE", entity="REC", entity_id="TEST_AS_EID_B", commit=False)
    db_session.commit()

    trail = service.get_audit_trail(entity_id="TEST_AS_EID_A")
    assert len(trail) == 1
    assert trail[0].entity_id == "TEST_AS_EID_A"
    assert trail[0].action == "CREATE"


# ---------------------------------------------------------------------------
# 8. Filter by action
# ---------------------------------------------------------------------------

def test_8_filter_by_action(db_session: Session):
    """Verify get_audit_trail filters accurately by action."""
    service = AuditService(db=db_session)
    service.log_action(actor="A", action="EXCEPTION_APPROVED", entity="EXC", entity_id="TEST_AS_ACT_1", commit=False)
    service.log_action(actor="B", action="EXCEPTION_REJECTED", entity="EXC", entity_id="TEST_AS_ACT_2", commit=False)
    db_session.commit()

    trail = service.get_audit_trail(action="EXCEPTION_APPROVED")
    matching = [x for x in trail if x.entity_id in ("TEST_AS_ACT_1", "TEST_AS_ACT_2")]
    assert len(matching) == 1
    assert matching[0].entity_id == "TEST_AS_ACT_1"


# ---------------------------------------------------------------------------
# 9. Combined filters work
# ---------------------------------------------------------------------------

def test_9_combined_filters_work(db_session: Session):
    """Verify entity, entity_id, and action filters compose properly with AND semantics."""
    service = AuditService(db=db_session)
    service.log_action(actor="A", action="SYNC", entity="WEBHOOK", entity_id="TEST_AS_COMB_1", commit=False)
    service.log_action(actor="B", action="SYNC", entity="TRANSACTION", entity_id="TEST_AS_COMB_1", commit=False)
    service.log_action(actor="C", action="REVERT", entity="WEBHOOK", entity_id="TEST_AS_COMB_1", commit=False)
    db_session.commit()

    trail = service.get_audit_trail(entity="WEBHOOK", entity_id="TEST_AS_COMB_1", action="SYNC")
    assert len(trail) == 1
    assert trail[0].actor == "A"
    assert trail[0].entity == "WEBHOOK"
    assert trail[0].action == "SYNC"


# ---------------------------------------------------------------------------
# 10. Constructor DB session works
# ---------------------------------------------------------------------------

def test_10_constructor_db_session_works(db_session: Session):
    """Verify AuditService works when db is passed to __init__ and omitted from methods."""
    service = AuditService(db=db_session)
    entry = service.log_action(
        actor="SYSTEM",
        action="TEST_INIT_DB",
        entity="TEST",
        entity_id="TEST_AS_INIT_001",
        commit=True,
    )

    trail = service.get_audit_trail(entity_id="TEST_AS_INIT_001")
    assert len(trail) == 1
    assert trail[0].audit_id == entry.audit_id


# ---------------------------------------------------------------------------
# 11. Per-call DB session overrides constructor session
# ---------------------------------------------------------------------------

def test_11_per_call_db_session_overrides_constructor():
    """Verify passing db to log_action/get_audit_trail overrides self.db."""
    dummy_session = MagicMock(spec=Session)
    service = AuditService(db=dummy_session)

    call_session = MagicMock(spec=Session)
    service.log_action(
        actor="OPERATOR",
        action="OVERRIDE",
        entity="MANUAL",
        entity_id="TEST_AS_OVER_001",
        db=call_session,
        commit=False,
    )

    # dummy_session must NOT be touched
    dummy_session.add.assert_not_called()
    # call_session must be used
    call_session.add.assert_called_once()

    # Similarly for get_audit_trail
    service.get_audit_trail(entity="MANUAL", db=call_session)
    call_session.query.assert_called_once()
    dummy_session.query.assert_not_called()


# ---------------------------------------------------------------------------
# 12. Audit IDs are unique
# ---------------------------------------------------------------------------

def test_12_audit_ids_are_unique(db_session: Session):
    """Verify every created AuditLog gets a unique audit_id."""
    service = AuditService(db=db_session)
    ids = set()
    for i in range(50):
        entry = service.log_action(
            actor="SYSTEM",
            action="BATCH_ACTION",
            entity="BATCH",
            entity_id=f"TEST_AS_BATCH_{i}",
            commit=False,
        )
        assert entry.audit_id not in ids
        assert entry.audit_id.startswith("AUD_")
        ids.add(entry.audit_id)
    assert len(ids) == 50


# ---------------------------------------------------------------------------
# 13. Missing DB session raises ValueError
# ---------------------------------------------------------------------------

def test_13_missing_db_session_raises_value_error():
    """Verify log_action and get_audit_trail raise ValueError when no session is available."""
    service = AuditService(db=None)
    with pytest.raises(ValueError, match="A database session .* is required to log"):
        service.log_action(actor="A", action="B", entity="C", entity_id="D")

    with pytest.raises(ValueError, match="A database session .* is required to query"):
        service.get_audit_trail(entity="C")
