"""
Phase 17 Task 4 — FastAPI Robustness & Security Test Suite
Verifies API boundary robustness, schema validation, and failure paths:
1. Webhook malformed/non-JSON bytes with valid HMAC rejected with HTTP 422
2. Webhook zero and negative amounts rejected with HTTP 422
3. Webhook missing required fields (event_id, payment_id) rejected with HTTP 422
4. Exception action with empty reviewer_id ("") rejected with HTTP 422
5. Webhook unsupported event_type rejected with HTTP 422
6. Transactions endpoint with invalid datetime format rejected with HTTP 422
"""

import json
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.main import app
from backend.config import settings
from backend.database import SessionLocal, init_db
from backend.models import (
    Transaction,
    WebhookEvent,
    ReconciliationException,
    ReconciliationResult,
    AuditLog,
)
from backend.services.security import generate_webhook_signature

client = TestClient(app)

TEST_PREFIX = "TEST_ROBUST_"


@pytest.fixture(scope="module", autouse=True)
def setup_robustness_db():
    """Initializes schema and cleans up test data before and after robustness tests."""
    init_db()
    db: Session = SessionLocal()
    try:
        db.query(AuditLog).filter(
            (AuditLog.entity_id.like(f"%{TEST_PREFIX}%")) | (AuditLog.audit_id.like(f"%{TEST_PREFIX}%"))
        ).delete(synchronize_session=False)
        db.query(WebhookEvent).filter(WebhookEvent.event_id.like(f"%{TEST_PREFIX}%")).delete(synchronize_session=False)
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like(f"%{TEST_PREFIX}%")).delete(synchronize_session=False)
        db.query(ReconciliationResult).filter(ReconciliationResult.reconciliation_id.like(f"%{TEST_PREFIX}%")).delete(synchronize_session=False)
        db.query(Transaction).filter(Transaction.transaction_id.like(f"%{TEST_PREFIX}%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()

    yield

    db = SessionLocal()
    try:
        db.query(AuditLog).filter(
            (AuditLog.entity_id.like(f"%{TEST_PREFIX}%")) | (AuditLog.audit_id.like(f"%{TEST_PREFIX}%"))
        ).delete(synchronize_session=False)
        db.query(WebhookEvent).filter(WebhookEvent.event_id.like(f"%{TEST_PREFIX}%")).delete(synchronize_session=False)
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like(f"%{TEST_PREFIX}%")).delete(synchronize_session=False)
        db.query(ReconciliationResult).filter(ReconciliationResult.reconciliation_id.like(f"%{TEST_PREFIX}%")).delete(synchronize_session=False)
        db.query(Transaction).filter(Transaction.transaction_id.like(f"%{TEST_PREFIX}%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


# =============================================================================
# 1. Webhook Malformed JSON Bytes Handling
# =============================================================================

def test_webhook_malformed_json_bytes_rejected_with_422():
    """
    Send syntactically invalid raw JSON bytes with a valid HMAC signature.
    Asserts:
    - HTTP 422 Unprocessable Entity
    - Error detail specifies malformed or invalid JSON payload
    - Zero WebhookEvent or Transaction records created in database
    """
    raw_corrupt_body = b'{"event_id": "TEST_ROBUST_EVT_CORRUPT", "amount": 1000.00, broken_json...'
    valid_sig = generate_webhook_signature(raw_corrupt_body, settings.WEBHOOK_SECRET)

    response = client.post(
        "/webhook/payment",
        content=raw_corrupt_body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": valid_sig,
        },
    )

    assert response.status_code == 422
    assert "malformed or invalid json payload" in response.json()["detail"].lower()

    # Verify no financial records or events persisted
    db = SessionLocal()
    try:
        wh_event = db.query(WebhookEvent).filter(WebhookEvent.event_id.like(f"%{TEST_PREFIX}%")).first()
        assert wh_event is None

        txn = db.query(Transaction).filter(Transaction.transaction_id.like(f"%{TEST_PREFIX}%")).first()
        assert txn is None
    finally:
        db.close()


# =============================================================================
# 2. Webhook Zero and Negative Amount Validation
# =============================================================================

def test_webhook_zero_and_negative_amount_rejected_with_422():
    """
    Submit webhook payloads with non-positive amounts (0.00 and -1500.00) signed with valid HMAC.
    Asserts:
    - HTTP 422 Unprocessable Entity for both amounts
    - Detail message explicitly identifies amount positivity constraint
    - No Transaction or WebhookEvent records created
    """
    invalid_amounts = [0.00, -1500.00]

    for idx, amt in enumerate(invalid_amounts):
        evt_id = f"{TEST_PREFIX}EVT_AMT_{idx}"
        pay_id = f"{TEST_PREFIX}PAY_AMT_{idx}"
        payload = {
            "event_id": evt_id,
            "event_type": "payment.captured",
            "payment_id": pay_id,
            "amount": amt,
            "currency": "INR",
        }
        raw_body = json.dumps(payload).encode("utf-8")
        valid_sig = generate_webhook_signature(raw_body, settings.WEBHOOK_SECRET)

        response = client.post(
            "/webhook/payment",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": valid_sig,
            },
        )

        assert response.status_code == 422
        assert "amount must be a positive number" in response.json()["detail"].lower()

        # Confirm no financial records created
        db = SessionLocal()
        try:
            assert db.query(WebhookEvent).filter_by(event_id=evt_id).first() is None
            assert db.query(Transaction).filter_by(transaction_id=pay_id).first() is None
        finally:
            db.close()


# =============================================================================
# 3. Webhook Missing Required Fields Validation
# =============================================================================

def test_webhook_missing_required_fields_rejected_with_422():
    """
    Submit webhook payloads missing required fields (event_id or payment_id) with valid HMAC.
    Asserts:
    - HTTP 422 Unprocessable Entity
    - No records created in database
    """
    # Case A: Missing payment_id
    payload_no_pay = {
        "event_id": f"{TEST_PREFIX}EVT_NOPAY",
        "event_type": "payment.captured",
        "amount": 2500.00,
        "currency": "INR",
    }
    raw_no_pay = json.dumps(payload_no_pay).encode("utf-8")
    sig_no_pay = generate_webhook_signature(raw_no_pay, settings.WEBHOOK_SECRET)

    resp_no_pay = client.post(
        "/webhook/payment",
        content=raw_no_pay,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": sig_no_pay,
        },
    )
    assert resp_no_pay.status_code == 422

    # Case B: Missing event_id
    payload_no_evt = {
        "event_type": "payment.captured",
        "payment_id": f"{TEST_PREFIX}PAY_NOEVT",
        "amount": 3000.00,
        "currency": "INR",
    }
    raw_no_evt = json.dumps(payload_no_evt).encode("utf-8")
    sig_no_evt = generate_webhook_signature(raw_no_evt, settings.WEBHOOK_SECRET)

    resp_no_evt = client.post(
        "/webhook/payment",
        content=raw_no_evt,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": sig_no_evt,
        },
    )
    assert resp_no_evt.status_code == 422

    # Confirm zero financial records created
    db = SessionLocal()
    try:
        assert db.query(WebhookEvent).filter(WebhookEvent.event_id.like(f"%{TEST_PREFIX}%")).first() is None
        assert db.query(Transaction).filter(Transaction.transaction_id.like(f"%{TEST_PREFIX}%")).first() is None
    finally:
        db.close()


# =============================================================================
# 4. Exception Action Empty Reviewer ID Validation
# =============================================================================

def test_exception_action_empty_reviewer_id_rejected_with_422():
    """
    Call POST /exceptions/{id}/approve and /reject with an empty reviewer_id ("").
    Asserts:
    - HTTP 422 Unprocessable Entity (min_length=1 enforcement)
    - Exception remains in OPEN status and unresolved
    - No resolution timestamp or resolved_by is populated
    """
    exc_id = f"{TEST_PREFIX}EXC_EMPTY_REV"
    db = SessionLocal()
    try:
        exc = ReconciliationException(
            exception_id=exc_id,
            reconciliation_id=None,
            transaction_id=f"{TEST_PREFIX}TXN_REV",
            category="AMOUNT_MISMATCH",
            severity="HIGH",
            difference_amount=500.00,
            status="OPEN",
        )
        db.add(exc)
        db.commit()
    finally:
        db.close()

    # 1. Attempt approval with empty reviewer_id
    resp_app = client.post(
        f"/exceptions/{exc_id}/approve",
        json={"reviewer_id": "", "notes": "Attempting empty reviewer"},
    )
    assert resp_app.status_code == 422

    # Verify exception remains OPEN
    db = SessionLocal()
    try:
        exc_reloaded = db.query(ReconciliationException).filter_by(exception_id=exc_id).first()
        assert exc_reloaded is not None
        assert exc_reloaded.status == "OPEN"
        assert exc_reloaded.resolved_by is None
        assert exc_reloaded.resolved_at is None
    finally:
        db.close()

    # 2. Attempt rejection with empty reviewer_id
    resp_rej = client.post(
        f"/exceptions/{exc_id}/reject",
        json={"reviewer_id": "", "notes": "Attempting empty reviewer"},
    )
    assert resp_rej.status_code == 422

    # Verify exception still remains OPEN
    db = SessionLocal()
    try:
        exc_reloaded2 = db.query(ReconciliationException).filter_by(exception_id=exc_id).first()
        assert exc_reloaded2 is not None
        assert exc_reloaded2.status == "OPEN"
        assert exc_reloaded2.resolved_by is None
    finally:
        db.close()


# =============================================================================
# 5. Webhook Unsupported Event Type Validation
# =============================================================================

def test_webhook_unsupported_event_type_rejected_with_422():
    """
    Submit webhook payload with an unsupported event_type signed with valid HMAC.
    Asserts:
    - HTTP 422 Unprocessable Entity
    - Detail specifies unsupported event_type
    - No WebhookEvent or Transaction records created
    """
    evt_id = f"{TEST_PREFIX}EVT_UNSUPPORTED"
    pay_id = f"{TEST_PREFIX}PAY_UNSUPPORTED"
    payload = {
        "event_id": evt_id,
        "event_type": "dispute.opened",  # Unsupported event
        "payment_id": pay_id,
        "amount": 1200.00,
        "currency": "INR",
    }
    raw_body = json.dumps(payload).encode("utf-8")
    valid_sig = generate_webhook_signature(raw_body, settings.WEBHOOK_SECRET)

    response = client.post(
        "/webhook/payment",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": valid_sig,
        },
    )

    assert response.status_code == 422
    assert "unsupported event_type" in response.json()["detail"].lower()

    # Verify no financial records created
    db = SessionLocal()
    try:
        assert db.query(WebhookEvent).filter_by(event_id=evt_id).first() is None
        assert db.query(Transaction).filter_by(transaction_id=pay_id).first() is None
    finally:
        db.close()


# =============================================================================
# 6. Transactions Invalid Datetime Query Parameter Validation
# =============================================================================

def test_transactions_invalid_datetime_query_param_rejected_with_422():
    """
    Call GET /transactions with an invalid datetime string for start_date and end_date.
    Asserts:
    - HTTP 422 Unprocessable Entity (FastAPI Query datetime coercion failure)
    - No financial mutation occurs
    """
    # 1. Invalid start_date
    resp_start = client.get("/transactions?start_date=not-a-timestamp")
    assert resp_start.status_code == 422

    # 2. Invalid end_date
    resp_end = client.get("/transactions?end_date=2026-99-99T99:99:99")
    assert resp_end.status_code == 422
