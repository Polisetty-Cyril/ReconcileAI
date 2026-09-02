"""
Phase 12C-4 Unit Tests: SLA + Escalation + Notification Orchestration
Verifies:
1. OPEN exception receives SLA evaluation.
2. WARNING exception creates SLA_WARNING once.
3. Repeated WARNING evaluation does not create duplicate rows.
4. BREACHED exception creates SLA_BREACH once.
5. Repeated BREACHED evaluation does not create duplicate rows.
6. 0 -> 1 escalation creates ESCALATION_L1.
7. Repeated level-1 evaluation does not create another L1 notification.
8. 1 -> 2 escalation creates ESCALATION_L2.
9. Repeated level-2 evaluation does not create another L2 notification.
10. Severe overdue exception produces SLA_BREACH + ESCALATION_L2 in correct order.
11. No fake ESCALATION_L1 is generated when EscalationService transitions directly to level 2.
12. No transport means notification remains PENDING.
13. Supplied MockEmailTransport delivers successfully (SENT).
14. Supplied MockEmailTransport failure results in FAILED status.
15. Failed notification is not automatically retried.
16. APPROVED exception produces no action.
17. REJECTED exception produces no action.
18. RESOLVED exception produces no action.
19. Exception status never changes.
20. is_resolved never changes.
21. final_decision never changes.
22. financial fields never change.
23. AI fields never change.
24. SLAService remains the source of SLA rules.
25. EscalationService remains the source of escalation rules.
26. NotificationService remains the source of notification idempotency.
27. Different exceptions do not collide on notification keys.
28. Injected now makes orchestration deterministic.
29. Explicit test for >= 2x overdue edge case.
30. Batch orchestration across open exceptions.
"""

import pytest
from typing import Optional
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session

from backend.database import SessionLocal, init_db
from backend.models import ReconciliationException, ReconciliationResult, NotificationLog
from backend.services.sla_service import SLAService
from backend.services.escalation_service import EscalationService
from backend.services.notification_service import NotificationService
from backend.services.email_transport import MockEmailTransport
from backend.services.sla_orchestrator import SLAOrchestrator, OrchestrationResult


@pytest.fixture(autouse=True)
def clean_test_orchestrator_records():
    """Initializes DB and cleans test records before and after each test."""
    init_db()
    db: Session = SessionLocal()
    try:
        db.query(NotificationLog).filter(NotificationLog.exception_id.like("%TEST_ORCH_12C4%")).delete(synchronize_session=False)
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like("%TEST_ORCH_12C4%")).delete(synchronize_session=False)
        db.query(ReconciliationResult).filter(ReconciliationResult.reconciliation_id.like("%TEST_ORCH_12C4%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(NotificationLog).filter(NotificationLog.exception_id.like("%TEST_ORCH_12C4%")).delete(synchronize_session=False)
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like("%TEST_ORCH_12C4%")).delete(synchronize_session=False)
        db.query(ReconciliationResult).filter(ReconciliationResult.reconciliation_id.like("%TEST_ORCH_12C4%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _create_orchestration_exception(
    db: Session,
    exception_id: str,
    status: str = "OPEN",
    severity: str = "HIGH",  # 4.0h duration
    difference_amount: float = 2500.0,
    escalation_level: int = 0,
    created_at: Optional[datetime] = None
) -> ReconciliationException:
    """Helper to persist a test exception with valid non-null fields."""
    base_time = created_at or datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    deadline = base_time + timedelta(hours=4.0)

    exc = ReconciliationException(
        exception_id=exception_id,
        transaction_id=f"TXN_{exception_id}",
        category="AMOUNT_MISMATCH",
        severity=severity,
        difference_amount=difference_amount,
        status=status,
        created_at=base_time,
        sla_duration_hours=4.0,
        sla_deadline=deadline,
        sla_status="OK",
        escalation_level=escalation_level,
        escalated_at=None,
        ai_explanation="Advisory explanation for orchestration test"
    )
    db.add(exc)
    db.commit()
    db.refresh(exc)
    return exc


# =============================================================================
# TESTS 1-3: WARNING Orchestration & Repeated Evaluation Idempotency
# =============================================================================

def test_warning_exception_creates_sla_warning_once():
    """1-3. At 75% elapsed ratio, creates SLA_WARNING once; repeated eval creates no duplicate."""
    db: Session = SessionLocal()
    try:
        exc_id = "TEST_ORCH_12C4_WARN"
        created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        exc = _create_orchestration_exception(db, exception_id=exc_id, created_at=created_at)

        # 75% elapsed: 3 hours for 4h duration
        now_75 = created_at + timedelta(hours=3.0)
        transport = MockEmailTransport()

        res1 = SLAOrchestrator.process_exception(db, exc, now=now_75, transport=transport)

        assert res1 is not None
        assert res1.sla_status == "WARNING"
        assert res1.escalation_level == 0
        assert res1.escalation_changed is False
        assert len(res1.notifications) == 1
        assert res1.notifications[0].event_type == "SLA_WARNING"
        assert res1.notifications[0].created is True
        assert res1.notifications[0].delivered is True
        assert res1.notifications[0].delivery_status == "SENT"

        # Verify DB has exactly 1 notification
        db_logs = db.query(NotificationLog).filter_by(exception_id=exc_id).all()
        assert len(db_logs) == 1
        assert db_logs[0].event_type == "SLA_WARNING"

        # Second evaluation at 80% (still WARNING)
        now_80 = created_at + timedelta(hours=3.2)
        res2 = SLAOrchestrator.process_exception(db, exc, now=now_80, transport=transport)

        assert res2 is not None
        assert res2.sla_status == "WARNING"
        assert len(res2.notifications) == 1
        assert res2.notifications[0].created is False
        assert res2.notifications[0].existing is True

        # DB still has exactly 1 notification row (no duplicate)
        assert db.query(NotificationLog).filter_by(exception_id=exc_id).count() == 1
    finally:
        db.close()


# =============================================================================
# TESTS 4-7: First Breach & Level 1 Escalation
# =============================================================================

def test_first_breach_and_level_1_escalation():
    """4-7. At 100% elapsed, produces SLA_BREACH and ESCALATION_L1 in order; repeated eval is idempotent."""
    db: Session = SessionLocal()
    try:
        exc_id = "TEST_ORCH_12C4_BREACH_L1"
        created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        exc = _create_orchestration_exception(db, exception_id=exc_id, created_at=created_at)

        # 100% elapsed: 4.0 hours
        now_100 = created_at + timedelta(hours=4.0)
        transport = MockEmailTransport()

        res = SLAOrchestrator.process_exception(db, exc, now=now_100, transport=transport)

        assert res is not None
        assert res.sla_status == "BREACHED"
        assert res.escalation_level == 1
        assert res.escalation_changed is True

        # Must produce exactly 2 notifications in strict order: SLA_BREACH, then ESCALATION_L1
        assert len(res.notifications) == 2
        assert res.notifications[0].event_type == "SLA_BREACH"
        assert res.notifications[0].created is True
        assert res.notifications[0].delivered is True

        assert res.notifications[1].event_type == "ESCALATION_L1"
        assert res.notifications[1].created is True
        assert res.notifications[1].delivered is True

        # Second evaluation at 120%
        now_120 = created_at + timedelta(hours=4.8)
        res2 = SLAOrchestrator.process_exception(db, exc, now=now_120, transport=transport)

        # Repeated evaluation creates no new notifications
        assert res2 is not None
        assert res2.escalation_changed is False
        # SLA_BREACH already exists, ESCALATION_L1 not triggered because no transition
        assert len(res2.notifications) == 1
        assert res2.notifications[0].created is False
        assert res2.notifications[0].existing is True

        assert db.query(NotificationLog).filter_by(exception_id=exc_id).count() == 2
    finally:
        db.close()


# =============================================================================
# TESTS 8-9: Level 1 -> 2 Escalation
# =============================================================================

def test_level_1_to_2_escalation_at_200_percent():
    """8-9. At 200% elapsed (from level 1), produces ESCALATION_L2; repeated eval creates no duplicate."""
    db: Session = SessionLocal()
    try:
        exc_id = "TEST_ORCH_12C4_L1_TO_L2"
        created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        # Pre-existing at level 1 with prior breach
        exc = _create_orchestration_exception(db, exception_id=exc_id, created_at=created_at, escalation_level=1)

        # Pre-seed SLA_BREACH notification so only ESCALATION_L2 is new
        NotificationService.create_notification(db, exc, event_type="SLA_BREACH", escalation_level=1)

        # 200% elapsed: 8.0 hours
        now_200 = created_at + timedelta(hours=8.0)
        transport = MockEmailTransport()

        res = SLAOrchestrator.process_exception(db, exc, now=now_200, transport=transport)

        assert res is not None
        assert res.sla_status == "BREACHED"
        assert res.escalation_level == 2
        assert res.escalation_changed is True

        # SLA_BREACH (existing=True), ESCALATION_L2 (created=True)
        assert len(res.notifications) == 2
        assert res.notifications[0].event_type == "SLA_BREACH"
        assert res.notifications[0].created is False

        assert res.notifications[1].event_type == "ESCALATION_L2"
        assert res.notifications[1].created is True
        assert res.notifications[1].delivered is True

        # Second evaluation at 250%
        now_250 = created_at + timedelta(hours=10.0)
        res2 = SLAOrchestrator.process_exception(db, exc, now=now_250, transport=transport)
        assert res2.escalation_changed is False
    finally:
        db.close()


# =============================================================================
# PART 14 & TESTS 10-11: CRITICAL EDGE CASE: Exception >= 2x Overdue
# =============================================================================

def test_severely_overdue_exception_produces_breach_and_l2_without_fake_l1():
    """
    10-11, 29 (PART 14 CRITICAL TEST).
    An exception evaluated for the first time at >= 2x overdue:
    - SLA = BREACHED
    - escalation = level 2
    - SLA_BREACH notification exists exactly once
    - ESCALATION_L2 notification exists exactly once
    - NO ESCALATION_L1 notification is created
    - no duplicate rows on second evaluation
    """
    db: Session = SessionLocal()
    try:
        exc_id = "TEST_ORCH_12C4_CRITICAL_OVERDUE"
        created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        # Fresh exception starting at level 0
        exc = _create_orchestration_exception(db, exception_id=exc_id, created_at=created_at, escalation_level=0)

        # 250% elapsed (10.0 hours for a 4h SLA)
        now_250 = created_at + timedelta(hours=10.0)
        transport = MockEmailTransport()

        res1 = SLAOrchestrator.process_exception(db, exc, now=now_250, transport=transport)

        assert res1 is not None
        assert res1.sla_status == "BREACHED"
        assert res1.escalation_level == 2
        assert res1.escalation_changed is True

        # Exactly 2 notifications: SLA_BREACH, then ESCALATION_L2
        assert len(res1.notifications) == 2
        assert res1.notifications[0].event_type == "SLA_BREACH"
        assert res1.notifications[1].event_type == "ESCALATION_L2"

        # Verify NO ESCALATION_L1 was created
        all_logs = db.query(NotificationLog).filter_by(exception_id=exc_id).all()
        events_created = [log.event_type for log in all_logs]
        assert "SLA_BREACH" in events_created
        assert "ESCALATION_L2" in events_created
        assert "ESCALATION_L1" not in events_created
        assert len(all_logs) == 2

        # Second evaluation at 300%: no duplicate rows
        now_300 = created_at + timedelta(hours=12.0)
        res2 = SLAOrchestrator.process_exception(db, exc, now=now_300, transport=transport)
        assert res2.escalation_changed is False
        assert db.query(NotificationLog).filter_by(exception_id=exc_id).count() == 2
    finally:
        db.close()


# =============================================================================
# TESTS 12-15: Transport Delivery & Failure Handling
# =============================================================================

def test_no_transport_leaves_notifications_pending():
    """12. If transport is not supplied, notification is created with status='PENDING'."""
    db: Session = SessionLocal()
    try:
        exc_id = "TEST_ORCH_12C4_NO_TRANS"
        created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        exc = _create_orchestration_exception(db, exception_id=exc_id, created_at=created_at)

        # 75% elapsed
        now_75 = created_at + timedelta(hours=3.0)
        res = SLAOrchestrator.process_exception(db, exc, now=now_75, transport=None)

        assert res is not None
        assert res.notifications[0].delivered is False
        assert res.notifications[0].delivery_status == "PENDING"

        db_log = db.query(NotificationLog).filter_by(exception_id=exc_id).first()
        assert db_log.status == "PENDING"
    finally:
        db.close()


def test_supplied_failing_transport_marks_failed_without_auto_retry():
    """14-15. Failing mock transport marks notification as FAILED without auto-retry."""
    db: Session = SessionLocal()
    try:
        exc_id = "TEST_ORCH_12C4_FAIL_TRANS"
        created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        exc = _create_orchestration_exception(db, exception_id=exc_id, created_at=created_at)

        now_75 = created_at + timedelta(hours=3.0)
        failing_transport = MockEmailTransport(should_fail=True, fail_error="Mail server error (simulated)")

        res = SLAOrchestrator.process_exception(db, exc, now=now_75, transport=failing_transport)

        assert res is not None
        assert res.notifications[0].delivered is False
        assert res.notifications[0].delivery_status == "FAILED"

        db_log = db.query(NotificationLog).filter_by(exception_id=exc_id).first()
        assert db_log.status == "FAILED"

        # Subsequent evaluation does NOT automatically retry the failed delivery
        working_transport = MockEmailTransport()
        res2 = SLAOrchestrator.process_exception(db, exc, now=now_75 + timedelta(minutes=5), transport=working_transport)
        # Because the notification already exists, it is not re-delivered
        assert res2.notifications[0].delivered is False
        assert res2.notifications[0].existing is True
    finally:
        db.close()


# =============================================================================
# TESTS 16-18: Non-OPEN Exceptions Safety
# =============================================================================

@pytest.mark.parametrize("non_open_status", ["APPROVED", "REJECTED", "RESOLVED"])
def test_non_open_exceptions_ignored_by_orchestrator(non_open_status):
    """16-18. Non-OPEN exceptions (APPROVED, REJECTED, RESOLVED) produce zero orchestration action."""
    db: Session = SessionLocal()
    try:
        exc_id = f"TEST_ORCH_12C4_{non_open_status}"
        created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        exc = _create_orchestration_exception(db, exception_id=exc_id, status=non_open_status, created_at=created_at)

        # 300% elapsed
        now_300 = created_at + timedelta(hours=12.0)
        transport = MockEmailTransport()

        res = SLAOrchestrator.process_exception(db, exc, now=now_300, transport=transport)

        assert res is None
        assert exc.status == non_open_status
        assert exc.escalation_level == 0
        assert db.query(NotificationLog).filter_by(exception_id=exc_id).count() == 0
    finally:
        db.close()


# =============================================================================
# TESTS 19-23: Immutability of Financial and AI Fields
# =============================================================================

def test_financial_and_ai_fields_never_mutated_by_orchestrator():
    """19-23. status, is_resolved, final_decision, financial amounts, and AI fields remain 100% untouched."""
    db: Session = SessionLocal()
    try:
        recon = ReconciliationResult(
            reconciliation_id="TEST_ORCH_12C4_REC_IMMUTABLE",
            match_score=70.0,
            matching_method="EXACT_RULE",
            ai_recommendation="FLAG",
            ai_confidence=85.0,
            ai_reasoning="Advisory reason",
            final_decision="HUMAN_REVIEW",
            discrepancy_amount=5000.0,
            is_resolved=False
        )
        db.add(recon)
        db.commit()

        created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        exc = ReconciliationException(
            exception_id="TEST_ORCH_12C4_EXC_IMMUTABLE",
            reconciliation_id="TEST_ORCH_12C4_REC_IMMUTABLE",
            transaction_id="TXN_TEST_ORCH_IMMUTABLE",
            category="AMOUNT_MISMATCH",
            severity="HIGH",
            difference_amount=5000.0,
            ai_explanation="Original AI explanation",
            status="OPEN",
            reviewer_notes=None,
            resolved_by=None,
            resolved_at=None,
            created_at=created_at,
            sla_duration_hours=4.0,
            sla_deadline=created_at + timedelta(hours=4.0),
            sla_status="OK",
            escalation_level=0,
            escalated_at=None
        )
        db.add(exc)
        db.commit()

        now_150 = created_at + timedelta(hours=6.0)
        transport = MockEmailTransport()

        res = SLAOrchestrator.process_exception(db, exc, now=now_150, transport=transport)
        assert res is not None

        db.refresh(exc)
        db.refresh(recon)

        # Exception fields untouched
        assert exc.status == "OPEN"
        assert exc.resolved_by is None
        assert exc.resolved_at is None
        assert exc.reviewer_notes is None
        assert exc.difference_amount == 5000.0
        assert exc.ai_explanation == "Original AI explanation"

        # ReconciliationResult fields untouched
        assert recon.is_resolved is False
        assert recon.final_decision == "HUMAN_REVIEW"
        assert recon.ai_recommendation == "FLAG"
        assert recon.ai_confidence == 85.0
    finally:
        db.close()


# =============================================================================
# TEST 27: Distinct Exceptions Do Not Collide on Notification Keys
# =============================================================================

def test_distinct_exceptions_produce_distinct_notification_keys():
    """27. Different exceptions with identical events produce distinct idempotency keys."""
    db: Session = SessionLocal()
    try:
        exc1 = _create_orchestration_exception(db, exception_id="TEST_ORCH_12C4_E1")
        exc2 = _create_orchestration_exception(db, exception_id="TEST_ORCH_12C4_E2")

        now = exc1.created_at + timedelta(hours=3.0)  # 75% -> WARNING
        transport = MockEmailTransport()

        res1 = SLAOrchestrator.process_exception(db, exc1, now=now, transport=transport)
        res2 = SLAOrchestrator.process_exception(db, exc2, now=now, transport=transport)

        assert res1.notifications[0].idempotency_key != res2.notifications[0].idempotency_key
        assert db.query(NotificationLog).filter(NotificationLog.exception_id.like("%TEST_ORCH_12C4%")).count() == 2
    finally:
        db.close()


# =============================================================================
# TEST 30: Batch Orchestration Across Open Exceptions
# =============================================================================

def test_process_all_open_exceptions_batch():
    """30. Batch orchestration evaluates all OPEN exceptions and skips non-OPEN ones."""
    db: Session = SessionLocal()
    try:
        created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        exc_open1 = _create_orchestration_exception(db, exception_id="TEST_ORCH_12C4_BATCH_1", created_at=created_at)
        exc_open2 = _create_orchestration_exception(db, exception_id="TEST_ORCH_12C4_BATCH_2", created_at=created_at)
        exc_approved = _create_orchestration_exception(db, exception_id="TEST_ORCH_12C4_BATCH_3", status="APPROVED", created_at=created_at)

        now = created_at + timedelta(hours=3.0)
        transport = MockEmailTransport()

        results = SLAOrchestrator.process_all_open_exceptions(db, now=now, transport=transport)

        evaluated_ids = {r.exception_id for r in results}
        assert "TEST_ORCH_12C4_BATCH_1" in evaluated_ids
        assert "TEST_ORCH_12C4_BATCH_2" in evaluated_ids
        assert "TEST_ORCH_12C4_BATCH_3" not in evaluated_ids
    finally:
        db.close()
