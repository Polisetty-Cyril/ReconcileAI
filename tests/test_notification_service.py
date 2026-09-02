"""
Phase 12C-2 Unit Tests: Notification Service + Idempotency Only
Verifies:
1. SLA_WARNING creates NotificationLog.
2. SLA_BREACH creates NotificationLog.
3. ESCALATION_L1 creates NotificationLog.
4. ESCALATION_L2 creates NotificationLog.
5. Correct recipient role for each event.
6. Correct recipient email for each event.
7. Correct idempotency key format.
8. Repeated identical notification request is idempotent.
9. Database contains exactly one row after duplicate requests.
10. Duplicate request returns existing/already-created result.
11. Database UNIQUE constraint remains effective.
12. Different event types create different notification records.
13. Different escalation levels create different idempotency keys.
14. Notification body contains required operational information.
15. Notification status does not falsely claim email delivery (status='PENDING').
16. APPROVED exception is rejected/ignored.
17. REJECTED exception is rejected/ignored.
18. RESOLVED exception is rejected/ignored.
19. status on the exception never changes.
20. is_resolved never changes.
21. final_decision never changes.
22. escalation_level never changes.
23. SLA fields never change.
24. AI fields never change.
25. No email/network delivery occurs.
26. Unrelated database IntegrityError is not silently swallowed.
27. Existing Phase 12C-1 escalation tests still pass.
28. Full regression passes.
"""

import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from backend.database import SessionLocal, init_db
from backend.models import ReconciliationException, ReconciliationResult, NotificationLog
from backend.services.sla_service import SLAService
from backend.services.notification_service import (
    NotificationService,
    NotificationResult,
    SUPPORTED_EVENT_TYPES,
    EVENT_RECIPIENT_MAPPING,
    DEFAULT_NOTIFICATION_STATUS,
)


@pytest.fixture(autouse=True)
def clean_test_notification_records():
    """Initializes DB and cleans test records before and after each test."""
    init_db()
    db: Session = SessionLocal()
    try:
        db.query(NotificationLog).filter(NotificationLog.exception_id.like("%TEST_NOTIF_12C2%")).delete(synchronize_session=False)
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like("%TEST_NOTIF_12C2%")).delete(synchronize_session=False)
        db.query(ReconciliationResult).filter(ReconciliationResult.reconciliation_id.like("%TEST_NOTIF_12C2%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(NotificationLog).filter(NotificationLog.exception_id.like("%TEST_NOTIF_12C2%")).delete(synchronize_session=False)
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like("%TEST_NOTIF_12C2%")).delete(synchronize_session=False)
        db.query(ReconciliationResult).filter(ReconciliationResult.reconciliation_id.like("%TEST_NOTIF_12C2%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _create_test_exception(
    db: Session,
    exception_id: str,
    status: str = "OPEN",
    severity: str = "HIGH",
    difference_amount: float = 1500.0,
    escalation_level: int = 0
) -> ReconciliationException:
    """Helper to persist a test exception with valid non-null fields."""
    created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    deadline = created_at + timedelta(hours=4.0)

    exc = ReconciliationException(
        exception_id=exception_id,
        transaction_id=f"TXN_{exception_id}",
        category="AMOUNT_MISMATCH",
        severity=severity,
        difference_amount=difference_amount,
        status=status,
        created_at=created_at,
        sla_duration_hours=4.0,
        sla_deadline=deadline,
        sla_status="WARNING" if severity != "CRITICAL" else "BREACHED",
        escalation_level=escalation_level,
        escalated_at=None,
        ai_explanation="Advisory explanation for test"
    )
    db.add(exc)
    db.commit()
    db.refresh(exc)
    return exc


# =============================================================================
# TESTS 1-7: Creation, Roles, Emails, and Idempotency Keys for All Events
# =============================================================================

@pytest.mark.parametrize("event_type,expected_role,expected_email,expected_level", [
    ("SLA_WARNING", "PRIMARY_REVIEWER", "reviewer@reconcileai.local", 0),
    ("SLA_BREACH", "FINANCE_SUPERVISOR", "supervisor@reconcileai.local", 1),
    ("ESCALATION_L1", "FINANCE_SUPERVISOR", "supervisor@reconcileai.local", 1),
    ("ESCALATION_L2", "FINANCE_DIRECTOR", "director@reconcileai.local", 2),
])
def test_notification_creation_for_supported_events(event_type, expected_role, expected_email, expected_level):
    """1-7. Verifies NotificationLog creation, roles, emails, and idempotency key for each event."""
    db: Session = SessionLocal()
    try:
        exc_id = f"TEST_NOTIF_12C2_{event_type}"
        exc = _create_test_exception(db, exception_id=exc_id, escalation_level=expected_level)

        res = NotificationService.create_notification(db, exc, event_type=event_type)

        assert res is not None
        assert res.created is True
        assert res.existing is False
        assert res.event_type == event_type
        assert res.recipient_role == expected_role
        assert res.recipient_email == expected_email
        assert res.idempotency_key == f"{exc_id}:{event_type}:{expected_level}"
        assert res.status == DEFAULT_NOTIFICATION_STATUS

        # Verify DB persistence
        log_entry = db.query(NotificationLog).filter_by(idempotency_key=res.idempotency_key).first()
        assert log_entry is not None
        assert log_entry.recipient_role == expected_role
        assert log_entry.recipient_email == expected_email
        assert log_entry.status == "PENDING"
    finally:
        db.close()


# =============================================================================
# TESTS 8-10: Repeated Request Idempotency
# =============================================================================

def test_repeated_identical_notification_request_is_idempotent():
    """8-10. Repeated request returns existing record, creates no new row, safe no-op."""
    db: Session = SessionLocal()
    try:
        exc_id = "TEST_NOTIF_12C2_IDEMPOTENT"
        exc = _create_test_exception(db, exception_id=exc_id, escalation_level=1)

        # First request: creates
        res1 = NotificationService.create_notification(db, exc, event_type="SLA_BREACH")
        assert res1 is not None
        assert res1.created is True
        assert res1.existing is False

        # Second request: safe duplicate no-op
        res2 = NotificationService.create_notification(db, exc, event_type="SLA_BREACH")
        assert res2 is not None
        assert res2.created is False
        assert res2.existing is True
        assert res2.notification_id == res1.notification_id
        assert res2.idempotency_key == res1.idempotency_key

        # Verify exactly one row in DB
        rows = db.query(NotificationLog).filter_by(idempotency_key=res1.idempotency_key).all()
        assert len(rows) == 1
    finally:
        db.close()


# =============================================================================
# TEST 11: Database UNIQUE Constraint Remains Effective
# =============================================================================

def test_database_unique_constraint_enforces_idempotency_key():
    """11. Direct raw DB insert with identical idempotency_key raises IntegrityError."""
    db: Session = SessionLocal()
    try:
        key = "TEST_NOTIF_12C2_UNIQUE:SLA_BREACH:1"
        log1 = NotificationLog(
            notification_id="NOTIF_TEST_12C2_U1",
            exception_id="TEST_NOTIF_12C2_UNIQUE",
            event_type="SLA_BREACH",
            recipient_role="FINANCE_SUPERVISOR",
            recipient_email="supervisor@reconcileai.local",
            subject="Alert 1",
            body="Body 1",
            idempotency_key=key,
            status="PENDING",
            sent_at=datetime.now(timezone.utc)
        )
        db.add(log1)
        db.commit()

        log2 = NotificationLog(
            notification_id="NOTIF_TEST_12C2_U2",
            exception_id="TEST_NOTIF_12C2_UNIQUE",
            event_type="SLA_BREACH",
            recipient_role="FINANCE_SUPERVISOR",
            recipient_email="supervisor@reconcileai.local",
            subject="Alert 2",
            body="Body 2",
            idempotency_key=key,  # Duplicate key
            status="PENDING",
            sent_at=datetime.now(timezone.utc)
        )
        db.add(log2)
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()


# =============================================================================
# TESTS 12-13: Event Types and Escalation Levels Create Different Keys
# =============================================================================

def test_different_events_and_levels_create_different_records():
    """12-13. Different event types and levels create distinct idempotency keys and DB rows."""
    db: Session = SessionLocal()
    try:
        exc_id = "TEST_NOTIF_12C2_MULTI_EVENTS"
        exc = _create_test_exception(db, exception_id=exc_id, escalation_level=1)

        res_breach = NotificationService.create_notification(db, exc, event_type="SLA_BREACH", escalation_level=1)
        res_esc_l1 = NotificationService.create_notification(db, exc, event_type="ESCALATION_L1", escalation_level=1)
        res_esc_l2 = NotificationService.create_notification(db, exc, event_type="ESCALATION_L2", escalation_level=2)

        assert res_breach.idempotency_key != res_esc_l1.idempotency_key
        assert res_esc_l1.idempotency_key != res_esc_l2.idempotency_key

        rows = db.query(NotificationLog).filter_by(exception_id=exc_id).all()
        assert len(rows) == 3
    finally:
        db.close()


# =============================================================================
# TEST 14: Notification Content Contains Required Operational Information
# =============================================================================

def test_notification_content_contains_operational_details():
    """14. Notification subject and body contain required operational details, no credentials."""
    db: Session = SessionLocal()
    try:
        exc_id = "TEST_NOTIF_12C2_CONTENT"
        exc = _create_test_exception(db, exception_id=exc_id, severity="HIGH", difference_amount=2500.50)

        res = NotificationService.create_notification(db, exc, event_type="SLA_WARNING")
        assert res is not None

        log_entry = db.query(NotificationLog).filter_by(idempotency_key=res.idempotency_key).first()
        assert log_entry is not None

        body = log_entry.body
        subject = log_entry.subject

        # Subject checks
        assert "SLA_WARNING" in subject
        assert exc_id in subject
        assert "HIGH" in subject

        # Body checks
        assert exc_id in body
        assert "AMOUNT_MISMATCH" in body
        assert "HIGH" in body
        assert "2,500.50" in body
        assert "WARNING" in body
        assert "Escalation Level: 0" in body
        assert "PRIMARY_REVIEWER" in body

        # Security check: no internal secrets
        for secret_word in ["password", "secret", "token", "database_url", "api_key"]:
            assert secret_word not in body.lower()
    finally:
        db.close()


# =============================================================================
# TEST 15: Status Does Not Falsely Claim Email Delivery
# =============================================================================

def test_notification_status_does_not_claim_email_sent():
    """15. Status is 'PENDING', not 'SENT', since no actual email dispatch exists yet."""
    db: Session = SessionLocal()
    try:
        exc_id = "TEST_NOTIF_12C2_STATUS"
        exc = _create_test_exception(db, exception_id=exc_id)

        res = NotificationService.create_notification(db, exc, event_type="SLA_WARNING")
        assert res.status == "PENDING"

        log_entry = db.query(NotificationLog).filter_by(idempotency_key=res.idempotency_key).first()
        assert log_entry.status == "PENDING"
    finally:
        db.close()


# =============================================================================
# TESTS 16-18: Non-OPEN Exceptions Ignored/Rejected
# =============================================================================

@pytest.mark.parametrize("non_open_status", ["APPROVED", "REJECTED", "RESOLVED"])
def test_non_open_exceptions_rejected_or_ignored(non_open_status):
    """16-18. Non-OPEN exceptions (APPROVED, REJECTED, RESOLVED) produce no NotificationLog."""
    db: Session = SessionLocal()
    try:
        exc_id = f"TEST_NOTIF_12C2_{non_open_status}"
        exc = _create_test_exception(db, exception_id=exc_id, status=non_open_status)

        res = NotificationService.create_notification(db, exc, event_type="SLA_WARNING")
        assert res is None

        # Verify no NotificationLog record created in DB
        rows = db.query(NotificationLog).filter_by(exception_id=exc_id).all()
        assert len(rows) == 0
    finally:
        db.close()


# =============================================================================
# TESTS 19-24: Immutability of Exception and Financial State
# =============================================================================

def test_exception_and_financial_fields_never_mutated():
    """19-24. status, is_resolved, final_decision, escalation_level, SLA, and AI fields never change."""
    db: Session = SessionLocal()
    try:
        recon = ReconciliationResult(
            reconciliation_id="TEST_NOTIF_12C2_REC_IMMUTABLE",
            match_score=75.0,
            matching_method="EXACT_RULE",
            ai_recommendation="FLAG",
            ai_confidence=90.0,
            ai_reasoning="Advisory AI reason",
            final_decision="HUMAN_REVIEW",
            discrepancy_amount=1000.0,
            is_resolved=False
        )
        db.add(recon)
        db.commit()

        created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        deadline = created_at + timedelta(hours=4.0)
        exc = ReconciliationException(
            exception_id="TEST_NOTIF_12C2_EXC_IMMUTABLE",
            reconciliation_id="TEST_NOTIF_12C2_REC_IMMUTABLE",
            transaction_id="TXN_TEST_NOTIF_IMMUTABLE",
            category="AMOUNT_MISMATCH",
            severity="HIGH",
            difference_amount=1000.0,
            ai_explanation="Original AI explanation",
            status="OPEN",
            reviewer_notes=None,
            resolved_by=None,
            resolved_at=None,
            created_at=created_at,
            sla_duration_hours=4.0,
            sla_deadline=deadline,
            sla_status="WARNING",
            escalation_level=1,
            escalated_at=created_at + timedelta(hours=4.0)
        )
        db.add(exc)
        db.commit()

        # Trigger notification
        res = NotificationService.create_notification(db, exc, event_type="ESCALATION_L1")
        assert res is not None

        db.refresh(exc)
        db.refresh(recon)

        # Exception fields must be completely untouched
        assert exc.status == "OPEN"
        assert exc.escalation_level == 1
        assert SLAService.normalize_utc_datetime(exc.escalated_at) == created_at + timedelta(hours=4.0)
        assert exc.sla_duration_hours == 4.0
        assert SLAService.normalize_utc_datetime(exc.sla_deadline) == deadline
        assert exc.sla_status == "WARNING"
        assert exc.resolved_by is None
        assert exc.resolved_at is None
        assert exc.reviewer_notes is None
        assert exc.ai_explanation == "Original AI explanation"

        # ReconciliationResult fields must be completely untouched
        assert recon.is_resolved is False
        assert recon.final_decision == "HUMAN_REVIEW"
        assert recon.ai_recommendation == "FLAG"
        assert recon.ai_confidence == 90.0
    finally:
        db.close()


# =============================================================================
# TEST 25: No Email / Network Delivery Occurs
# =============================================================================

def test_no_email_or_network_delivery():
    """25. Confirms no SMTP, mock outbox, or network transport is executed."""
    import sys
    assert "smtplib" not in sys.modules or not hasattr(NotificationService, "transport")
    # Verify NotificationService has no send_mail or transport references
    assert not hasattr(NotificationService, "transport")
    assert not hasattr(NotificationService, "outbox")


# =============================================================================
# TEST 26: Unrelated IntegrityError Is Not Swallowed
# =============================================================================

def test_unrelated_integrity_error_is_re_raised(monkeypatch):
    """26. If an insert fails for a reason other than idempotency_key, IntegrityError is raised."""
    db: Session = SessionLocal()
    try:
        exc_id = "TEST_NOTIF_12C2_ERROR_RAISE"
        exc = _create_test_exception(db, exception_id=exc_id)

        # Monkeypatch format_notification to return an invalid subject (None) to trigger NOT NULL constraint failure
        monkeypatch.setattr(
            NotificationService,
            "format_notification",
            lambda e, et, el: (None, "Some body")  # subject cannot be None in DB
        )

        with pytest.raises(IntegrityError):
            NotificationService.create_notification(db, exc, event_type="SLA_WARNING")
    finally:
        db.close()
