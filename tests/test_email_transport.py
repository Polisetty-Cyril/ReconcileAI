"""
Phase 12C-3 Unit Tests: Mock Email Transport + Delivery Handling Only
Verifies:
1. Mock transport succeeds deterministically.
2. Mock transport failure can be forced deterministically.
3. Successful delivery changes: PENDING -> SENT.
4. Successful delivery sets sent_at.
5. Failed delivery changes: PENDING -> FAILED.
6. Failed delivery leaves sent_at NULL / un-updated.
7. SENT notification cannot be delivered again (raises ValueError).
8. FAILED notification does not automatically retry (raises ValueError).
9. Notification ID remains unchanged.
10. Idempotency key remains unchanged.
11. No duplicate NotificationLog is created.
12. Recipient email is passed correctly.
13. Subject is passed correctly.
14. Body is passed correctly.
15. Exception financial/status fields remain unchanged.
16. No network/email library is used.
17. Missing notification ID is handled clearly (raises ValueError).
18. Invalid delivery state is handled clearly (raises ValueError).
19. Injected timestamp works deterministically.
20. Existing Phase 12C-2 tests still pass.
21. Existing Phase 12C-1 tests still pass.
22. Full regression passes.
"""

import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session

from backend.database import SessionLocal, init_db
from backend.models import ReconciliationException, ReconciliationResult, NotificationLog
from backend.services.sla_service import SLAService
from backend.services.email_transport import MockEmailTransport, EmailSendResult
from backend.services.notification_service import NotificationService, NotificationResult


@pytest.fixture(autouse=True)
def clean_test_delivery_records():
    """Initializes DB and cleans test records before and after each test."""
    init_db()
    db: Session = SessionLocal()
    try:
        db.query(NotificationLog).filter(NotificationLog.exception_id.like("%TEST_DELIV_12C3%")).delete(synchronize_session=False)
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like("%TEST_DELIV_12C3%")).delete(synchronize_session=False)
        db.query(ReconciliationResult).filter(ReconciliationResult.reconciliation_id.like("%TEST_DELIV_12C3%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(NotificationLog).filter(NotificationLog.exception_id.like("%TEST_DELIV_12C3%")).delete(synchronize_session=False)
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like("%TEST_DELIV_12C3%")).delete(synchronize_session=False)
        db.query(ReconciliationResult).filter(ReconciliationResult.reconciliation_id.like("%TEST_DELIV_12C3%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _create_pending_notification(
    db: Session,
    exception_id: str,
    event_type: str = "SLA_WARNING",
    escalation_level: int = 0
) -> tuple[ReconciliationException, NotificationResult]:
    """Helper to create an exception and its initial PENDING notification."""
    created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    deadline = created_at + timedelta(hours=4.0)

    exc = ReconciliationException(
        exception_id=exception_id,
        transaction_id=f"TXN_{exception_id}",
        category="AMOUNT_MISMATCH",
        severity="HIGH",
        difference_amount=1200.0,
        status="OPEN",
        created_at=created_at,
        sla_duration_hours=4.0,
        sla_deadline=deadline,
        sla_status="WARNING",
        escalation_level=escalation_level,
        escalated_at=None,
        ai_explanation="Advisory explanation for delivery test"
    )
    db.add(exc)
    db.commit()
    db.refresh(exc)

    notif_res = NotificationService.create_notification(
        db,
        exc,
        event_type=event_type,
        escalation_level=escalation_level
    )
    return exc, notif_res


# =============================================================================
# TESTS 1-2: Mock Transport Success & Deterministic Failure
# =============================================================================

def test_mock_transport_succeeds_deterministically():
    """1. Mock transport succeeds deterministically without network calls."""
    transport = MockEmailTransport()
    res = transport.send("supervisor@reconcileai.local", "Subject Test", "Body Test")

    assert res.success is True
    assert res.recipient_email == "supervisor@reconcileai.local"
    assert res.subject == "Subject Test"
    assert res.error is None
    assert len(transport.get_sent_messages()) == 1
    assert transport.sent_messages[0]["body"] == "Body Test"


def test_mock_transport_failure_forced_deterministically():
    """2. Mock transport failure can be forced deterministically."""
    transport = MockEmailTransport(should_fail=True, fail_error="Connection refused (mock)")
    res = transport.send("supervisor@reconcileai.local", "Subject Test", "Body Test")

    assert res.success is False
    assert res.error == "Connection refused (mock)"
    assert len(transport.get_sent_messages()) == 0


# =============================================================================
# TESTS 3-6: Lifecycle Transitions & Timestamp Behavior
# =============================================================================

def test_successful_delivery_transitions_pending_to_sent_and_sets_sent_at():
    """3-4, 19. Successful delivery changes PENDING -> SENT and sets sent_at to injected time."""
    db: Session = SessionLocal()
    try:
        exc_id = "TEST_DELIV_12C3_SUCCESS"
        _, notif_res = _create_pending_notification(db, exception_id=exc_id)

        transport = MockEmailTransport()
        delivery_time = datetime(2026, 9, 1, 11, 30, 0, tzinfo=timezone.utc)

        result = NotificationService.deliver_notification(
            db,
            notification_id=notif_res.notification_id,
            transport=transport,
            now=delivery_time
        )

        assert result.status == "SENT"
        assert result.delivery_success is True
        assert SLAService.normalize_utc_datetime(result.sent_at) == delivery_time

        # Verify DB persistence
        db_log = db.query(NotificationLog).filter_by(notification_id=notif_res.notification_id).first()
        assert db_log is not None
        assert db_log.status == "SENT"
        assert SLAService.normalize_utc_datetime(db_log.sent_at) == delivery_time
    finally:
        db.close()


def test_failed_delivery_transitions_pending_to_failed_and_leaves_sent_at_null():
    """5-6. Failed delivery changes PENDING -> FAILED and leaves result.sent_at NULL."""
    db: Session = SessionLocal()
    try:
        exc_id = "TEST_DELIV_12C3_FAIL"
        _, notif_res = _create_pending_notification(db, exception_id=exc_id)

        transport = MockEmailTransport(should_fail=True, fail_error="Mailbox unavailable (mock)")
        attempt_time = datetime(2026, 9, 1, 11, 45, 0, tzinfo=timezone.utc)

        result = NotificationService.deliver_notification(
            db,
            notification_id=notif_res.notification_id,
            transport=transport,
            now=attempt_time
        )

        assert result.status == "FAILED"
        assert result.delivery_success is False
        assert result.delivery_error == "Mailbox unavailable (mock)"
        assert result.sent_at is None

        # Verify DB persistence
        db_log = db.query(NotificationLog).filter_by(notification_id=notif_res.notification_id).first()
        assert db_log is not None
        assert db_log.status == "FAILED"
    finally:
        db.close()


# =============================================================================
# TESTS 7-8, 17-18: Invalid State Handling & No Automatic Retry
# =============================================================================

def test_sent_notification_cannot_be_delivered_again():
    """7, 18. A notification already in SENT status cannot be delivered again."""
    db: Session = SessionLocal()
    try:
        exc_id = "TEST_DELIV_12C3_NO_RESEND"
        _, notif_res = _create_pending_notification(db, exception_id=exc_id)

        transport = MockEmailTransport()
        NotificationService.deliver_notification(db, notif_res.notification_id, transport)

        # Attempt delivering a second time
        with pytest.raises(ValueError, match="Only notifications in 'PENDING' status can be delivered"):
            NotificationService.deliver_notification(db, notif_res.notification_id, transport)
    finally:
        db.close()


def test_failed_notification_does_not_automatically_retry():
    """8, 18. A notification in FAILED status raises ValueError rather than auto-retrying."""
    db: Session = SessionLocal()
    try:
        exc_id = "TEST_DELIV_12C3_NO_AUTO_RETRY"
        _, notif_res = _create_pending_notification(db, exception_id=exc_id)

        failing_transport = MockEmailTransport(should_fail=True)
        NotificationService.deliver_notification(db, notif_res.notification_id, failing_transport)

        # Subsequent call with working transport must raise ValueError
        working_transport = MockEmailTransport()
        with pytest.raises(ValueError, match="Only notifications in 'PENDING' status can be delivered"):
            NotificationService.deliver_notification(db, notif_res.notification_id, working_transport)
    finally:
        db.close()


def test_missing_notification_id_handled_clearly():
    """17. Missing notification ID raises clear ValueError."""
    db: Session = SessionLocal()
    try:
        transport = MockEmailTransport()
        with pytest.raises(ValueError, match="Notification with ID 'NON_EXISTENT_ID' not found"):
            NotificationService.deliver_notification(db, "NON_EXISTENT_ID", transport)
    finally:
        db.close()


# =============================================================================
# TESTS 9-14: Identity Preservation, Correct Content & No Duplicates
# =============================================================================

def test_delivery_preserves_notification_identity_and_passes_correct_payload():
    """9-14. Delivery preserves notification_id, idempotency_key, creates no duplicate row, passes fields."""
    db: Session = SessionLocal()
    try:
        exc_id = "TEST_DELIV_12C3_IDENTITY"
        _, notif_res = _create_pending_notification(db, exception_id=exc_id, event_type="SLA_BREACH", escalation_level=1)

        initial_id = notif_res.notification_id
        initial_key = notif_res.idempotency_key

        transport = MockEmailTransport()
        deliv_res = NotificationService.deliver_notification(db, initial_id, transport)

        # Identity preservation
        assert deliv_res.notification_id == initial_id
        assert deliv_res.idempotency_key == initial_key

        # Exactly 1 row in DB
        rows = db.query(NotificationLog).filter_by(notification_id=initial_id).all()
        assert len(rows) == 1

        # Correct payload passed to transport
        sent_messages = transport.get_sent_messages()
        assert len(sent_messages) == 1
        msg = sent_messages[0]

        assert msg["recipient_email"] == "supervisor@reconcileai.local"
        assert "[SLA_BREACH]" in msg["subject"]
        assert exc_id in msg["subject"]
        assert exc_id in msg["body"]
        assert "AMOUNT_MISMATCH" in msg["body"]
        assert "1,200.00" in msg["body"]
    finally:
        db.close()


# =============================================================================
# TEST 15: Financial & Exception Immutability
# =============================================================================

def test_delivery_never_mutates_exception_or_financial_fields():
    """15. Delivery leaves ReconciliationException and ReconciliationResult fields 100% untouched."""
    db: Session = SessionLocal()
    try:
        recon = ReconciliationResult(
            reconciliation_id="TEST_DELIV_12C3_REC_IMMUTABLE",
            match_score=80.0,
            matching_method="EXACT_RULE",
            ai_recommendation="FLAG",
            ai_confidence=88.0,
            ai_reasoning="Advisory AI reason",
            final_decision="HUMAN_REVIEW",
            discrepancy_amount=2000.0,
            is_resolved=False
        )
        db.add(recon)
        db.commit()

        created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        deadline = created_at + timedelta(hours=4.0)

        exc = ReconciliationException(
            exception_id="TEST_DELIV_12C3_EXC_IMMUTABLE",
            reconciliation_id="TEST_DELIV_12C3_REC_IMMUTABLE",
            transaction_id="TXN_TEST_DELIV_IMMUTABLE",
            category="AMOUNT_MISMATCH",
            severity="HIGH",
            difference_amount=2000.0,
            ai_explanation="Original AI explanation",
            status="OPEN",
            reviewer_notes=None,
            resolved_by=None,
            resolved_at=None,
            created_at=created_at,
            sla_duration_hours=4.0,
            sla_deadline=deadline,
            sla_status="WARNING",
            escalation_level=0,
            escalated_at=None
        )
        db.add(exc)
        db.commit()

        notif_res = NotificationService.create_notification(db, exc, event_type="SLA_WARNING")
        assert notif_res is not None

        # Execute mock delivery
        transport = MockEmailTransport()
        NotificationService.deliver_notification(db, notif_res.notification_id, transport)

        db.refresh(exc)
        db.refresh(recon)

        # Exception fields untouched
        assert exc.status == "OPEN"
        assert exc.escalation_level == 0
        assert exc.escalated_at is None
        assert exc.sla_duration_hours == 4.0
        assert SLAService.normalize_utc_datetime(exc.sla_deadline) == deadline
        assert exc.sla_status == "WARNING"
        assert exc.resolved_by is None
        assert exc.resolved_at is None
        assert exc.reviewer_notes is None
        assert exc.ai_explanation == "Original AI explanation"

        # ReconciliationResult fields untouched
        assert recon.is_resolved is False
        assert recon.final_decision == "HUMAN_REVIEW"
        assert recon.ai_recommendation == "FLAG"
        assert recon.ai_confidence == 88.0
    finally:
        db.close()


# =============================================================================
# TEST 16: No Network / Real Email Libraries
# =============================================================================

def test_no_real_email_or_network_libraries_used():
    """16. Confirms no real network/email libraries (smtplib, requests, httpx) are imported by transport."""
    import backend.services.email_transport as et_mod
    with open(et_mod.__file__, "r", encoding="utf-8") as f:
        content = f.read()

    forbidden_tokens = ["smtplib", "EmailMessage", "requests", "httpx", "urllib.request", "sendgrid", "boto3"]
    for token in forbidden_tokens:
        assert token not in content, f"Forbidden library token '{token}' found in email_transport.py"
