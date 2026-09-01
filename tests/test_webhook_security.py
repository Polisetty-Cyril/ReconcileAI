"""
Phase 10 Unit & Integration Tests: Webhook Security, HMAC Verification & Idempotency
Verifies:
1. Valid HMAC SHA-256 signature verification over exact raw request body -> HTTP 200
2. Invalid HMAC SHA-256 signature rejection -> HTTP 401 + WEBHOOK_SIGNATURE_FAILED audit log
3. Missing X-Razorpay-Signature header rejection -> HTTP 401 + WEBHOOK_SIGNATURE_FAILED audit log
4. Missing / empty secret safe failure -> Rejects safely
5. Webhook idempotency protection:
   - First delivery succeeds (HTTP 200, WebhookEvent & Transaction created)
   - Duplicate delivery with same event_id is rejected -> HTTP 409 Conflict
   - No duplicate WebhookEvent or Transaction created
   - Original Transaction not modified
   - WEBHOOK_DUPLICATE_REJECTED audit log created
6. Mutated payload with duplicate event_id is safely rejected -> HTTP 409
7. Multiple replays do not corrupt database counts
8. Transaction atomicity: Unverified/duplicate payloads never create or mutate financial records
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
    AuditLog
)
from backend.services.security import generate_webhook_signature, verify_webhook_signature

client = TestClient(app)

@pytest.fixture(scope="module", autouse=True)
def setup_security_db():
    """Initializes schema and cleans up test data before and after security tests."""
    init_db()
    db: Session = SessionLocal()
    try:
        db.query(AuditLog).filter((AuditLog.entity_id.like("%TEST_SEC%")) | (AuditLog.audit_id.like("%TEST_SEC%"))).delete(synchronize_session=False)
        db.query(WebhookEvent).filter(WebhookEvent.event_id.like("%TEST_SEC%")).delete(synchronize_session=False)
        db.query(Transaction).filter(Transaction.transaction_id.like("%TEST_SEC%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(AuditLog).filter((AuditLog.entity_id.like("%TEST_SEC%")) | (AuditLog.audit_id.like("%TEST_SEC%"))).delete(synchronize_session=False)
        db.query(WebhookEvent).filter(WebhookEvent.event_id.like("%TEST_SEC%")).delete(synchronize_session=False)
        db.query(Transaction).filter(Transaction.transaction_id.like("%TEST_SEC%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()

# -----------------------------------------------------------------------------
# 1. Cryptographic Unit Tests for Security Utilities
# -----------------------------------------------------------------------------

def test_generate_and_verify_signature_unit():
    """Verify that generate_webhook_signature produces valid HMAC and verify_webhook_signature validates it."""
    secret = "test_secret_key_123"
    raw_body = b'{"event_id":"evt_unit_001","amount":1000.0}'
    
    sig = generate_webhook_signature(raw_body, secret)
    assert isinstance(sig, str)
    assert len(sig) == 64  # SHA-256 hex string length
    
    # Valid verification
    assert verify_webhook_signature(raw_body, sig, secret) is True
    
    # Invalid signature
    assert verify_webhook_signature(raw_body, "invalid_signature_hex", secret) is False
    
    # Mutated body
    mutated_body = b'{"event_id":"evt_unit_001","amount":9999.0}'
    assert verify_webhook_signature(mutated_body, sig, secret) is False

def test_verify_signature_missing_or_empty_secret_fails_safely():
    """Verify that empty, None, or whitespace-only secret safely fails verification."""
    raw_body = b'{"event_id":"evt_unit_002"}'
    sig = "some_signature_value"
    
    assert verify_webhook_signature(raw_body, sig, "") is False
    assert verify_webhook_signature(raw_body, sig, None) is False
    assert verify_webhook_signature(raw_body, sig, "   ") is False
    assert verify_webhook_signature(raw_body, None, "valid_secret") is False
    assert verify_webhook_signature(raw_body, "", "valid_secret") is False

# -----------------------------------------------------------------------------
# 2. HTTP Endpoint HMAC Verification Tests
# -----------------------------------------------------------------------------

def test_webhook_valid_signature_success():
    """Verify valid signature over raw body results in HTTP 200, WebhookEvent, Transaction, and AuditLog."""
    payload = {
        "event_id": "TEST_SEC_EVT_001",
        "event_type": "payment.captured",
        "payment_id": "TEST_SEC_PAY_001",
        "order_id": "ORD_SEC_001",
        "amount": 3500.00,
        "currency": "INR",
        "description": "Secure valid payment webhook"
    }
    raw_body = json.dumps(payload).encode("utf-8")
    valid_sig = generate_webhook_signature(raw_body, settings.WEBHOOK_SECRET)
    
    response = client.post(
        "/webhook/payment",
        content=raw_body,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": valid_sig}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["event_id"] == "TEST_SEC_EVT_001"
    assert data["transaction_id"] == "TEST_SEC_PAY_001"

    # Verify DB persistence
    db = SessionLocal()
    try:
        wh_event = db.query(WebhookEvent).filter_by(event_id="TEST_SEC_EVT_001").first()
        assert wh_event is not None
        assert wh_event.signature == valid_sig
        assert wh_event.is_processed is True

        txn = db.query(Transaction).filter_by(transaction_id="TEST_SEC_PAY_001", source="GATEWAY").first()
        assert txn is not None
        assert txn.amount == 3500.00
        assert txn.status == "CAPTURED"

        audit = db.query(AuditLog).filter_by(entity="WEBHOOK", entity_id="TEST_SEC_EVT_001").first()
        assert audit is not None
        assert audit.action == "WEBHOOK_RECEIVED"
    finally:
        db.close()

def test_webhook_invalid_signature_rejected():
    """Verify invalid signature results in HTTP 401 and WEBHOOK_SIGNATURE_FAILED audit log."""
    payload = {
        "event_id": "TEST_SEC_EVT_002",
        "event_type": "payment.captured",
        "payment_id": "TEST_SEC_PAY_002",
        "amount": 2000.00,
        "currency": "INR"
    }
    raw_body = json.dumps(payload).encode("utf-8")
    invalid_sig = "deadbeef1234567890abcdefdeadbeef1234567890abcdefdeadbeef12345678"

    response = client.post(
        "/webhook/payment",
        content=raw_body,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": invalid_sig}
    )
    assert response.status_code == 401
    assert "signature" in response.json()["detail"].lower()

    # Verify NO transaction and NO successful WebhookEvent were created
    db = SessionLocal()
    try:
        wh_event = db.query(WebhookEvent).filter_by(event_id="TEST_SEC_EVT_002").first()
        assert wh_event is None

        txn = db.query(Transaction).filter_by(transaction_id="TEST_SEC_PAY_002").first()
        assert txn is None

        # Verify WEBHOOK_SIGNATURE_FAILED audit log
        audit = db.query(AuditLog).filter_by(action="WEBHOOK_SIGNATURE_FAILED", entity_id="TEST_SEC_EVT_002").first()
        assert audit is not None
        assert audit.actor == "WEBHOOK_GATEWAY"
    finally:
        db.close()

def test_webhook_missing_signature_header_rejected():
    """Verify missing signature header results in HTTP 401."""
    payload = {
        "event_id": "TEST_SEC_EVT_003",
        "event_type": "payment.captured",
        "payment_id": "TEST_SEC_PAY_003",
        "amount": 1000.00
    }
    raw_body = json.dumps(payload).encode("utf-8")

    response = client.post(
        "/webhook/payment",
        content=raw_body,
        headers={"Content-Type": "application/json"}  # No X-Razorpay-Signature
    )
    assert response.status_code == 401
    assert "signature" in response.json()["detail"].lower()

    # Ensure no transaction was created
    db = SessionLocal()
    try:
        txn = db.query(Transaction).filter_by(transaction_id="TEST_SEC_PAY_003").first()
        assert txn is None
    finally:
        db.close()

def test_webhook_tampered_payload_rejected():
    """Verify that tampering with a single byte of body after signature generation is rejected with HTTP 401."""
    original_payload = {
        "event_id": "TEST_SEC_EVT_004",
        "event_type": "payment.captured",
        "payment_id": "TEST_SEC_PAY_004",
        "amount": 100.00
    }
    raw_original = json.dumps(original_payload).encode("utf-8")
    sig = generate_webhook_signature(raw_original, settings.WEBHOOK_SECRET)

    # Tampered body with amount changed to 10000.00
    tampered_payload = {
        "event_id": "TEST_SEC_EVT_004",
        "event_type": "payment.captured",
        "payment_id": "TEST_SEC_PAY_004",
        "amount": 10000.00
    }
    raw_tampered = json.dumps(tampered_payload).encode("utf-8")

    response = client.post(
        "/webhook/payment",
        content=raw_tampered,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": sig}
    )
    assert response.status_code == 401

# -----------------------------------------------------------------------------
# 3. Idempotency & Duplicate Protection Tests
# -----------------------------------------------------------------------------

def test_webhook_idempotency_duplicate_event_rejected():
    """Verify that resubmitting a processed event_id returns HTTP 409 and logs WEBHOOK_DUPLICATE_REJECTED."""
    payload = {
        "event_id": "TEST_SEC_EVT_DUP_001",
        "event_type": "payment.captured",
        "payment_id": "TEST_SEC_PAY_DUP_001",
        "order_id": "ORD_SEC_DUP_001",
        "amount": 7500.00,
        "currency": "INR",
        "description": "Idempotency primary delivery"
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = generate_webhook_signature(raw_body, settings.WEBHOOK_SECRET)
    headers = {"Content-Type": "application/json", "X-Razorpay-Signature": sig}

    # 1. First delivery -> HTTP 200
    res1 = client.post("/webhook/payment", content=raw_body, headers=headers)
    assert res1.status_code == 200

    db = SessionLocal()
    try:
        events_count_1 = db.query(WebhookEvent).filter_by(event_id="TEST_SEC_EVT_DUP_001").count()
        txns_count_1 = db.query(Transaction).filter_by(transaction_id="TEST_SEC_PAY_DUP_001").count()
        assert events_count_1 == 1
        assert txns_count_1 == 1
    finally:
        db.close()

    # 2. Second delivery with exact same event_id -> HTTP 409 Conflict
    res2 = client.post("/webhook/payment", content=raw_body, headers=headers)
    assert res2.status_code == 409
    assert "duplicate" in res2.json()["detail"].lower()

    # 3. Verify counts in DB remain strictly 1
    db = SessionLocal()
    try:
        events_count_2 = db.query(WebhookEvent).filter_by(event_id="TEST_SEC_EVT_DUP_001").count()
        txns_count_2 = db.query(Transaction).filter_by(transaction_id="TEST_SEC_PAY_DUP_001").count()
        assert events_count_2 == 1
        assert txns_count_2 == 1

        # Verify WEBHOOK_DUPLICATE_REJECTED audit entry
        dup_audit = db.query(AuditLog).filter_by(
            action="WEBHOOK_DUPLICATE_REJECTED",
            entity_id="TEST_SEC_EVT_DUP_001"
        ).first()
        assert dup_audit is not None
        assert dup_audit.actor == "WEBHOOK_GATEWAY"
    finally:
        db.close()

def test_webhook_mutated_payload_duplicate_event_rejected():
    """Verify that submitting a different payload with an already-used event_id is rejected without modifying original data."""
    # Step 1: Ingest first event
    payload1 = {
        "event_id": "TEST_SEC_EVT_MUT_001",
        "event_type": "payment.captured",
        "payment_id": "TEST_SEC_PAY_MUT_001",
        "amount": 500.00,
        "currency": "INR",
        "description": "Original unmutated"
    }
    raw1 = json.dumps(payload1).encode("utf-8")
    sig1 = generate_webhook_signature(raw1, settings.WEBHOOK_SECRET)
    res1 = client.post("/webhook/payment", content=raw1, headers={"Content-Type": "application/json", "X-Razorpay-Signature": sig1})
    assert res1.status_code == 200

    # Step 2: Attempt duplicate submission with mutated payment_id and amount
    mutated_payload = {
        "event_id": "TEST_SEC_EVT_MUT_001",  # Same event_id
        "event_type": "payment.captured",
        "payment_id": "TEST_SEC_PAY_MUT_FRAUD",  # Mutated payment ID
        "amount": 99999.00,  # Mutated amount
        "currency": "INR",
        "description": "Fraudulent replay"
    }
    raw_mut = json.dumps(mutated_payload).encode("utf-8")
    sig_mut = generate_webhook_signature(raw_mut, settings.WEBHOOK_SECRET)
    res2 = client.post("/webhook/payment", content=raw_mut, headers={"Content-Type": "application/json", "X-Razorpay-Signature": sig_mut})
    assert res2.status_code == 409

    # Step 3: Verify original transaction was untouched and fraudulent transaction was not created
    db = SessionLocal()
    try:
        orig_txn = db.query(Transaction).filter_by(transaction_id="TEST_SEC_PAY_MUT_001").first()
        assert orig_txn is not None
        assert orig_txn.amount == 500.00  # Untouched

        fraud_txn = db.query(Transaction).filter_by(transaction_id="TEST_SEC_PAY_MUT_FRAUD").first()
        assert fraud_txn is None  # Not created
    finally:
        db.close()

def test_webhook_multiple_replays_remain_strictly_idempotent():
    """Verify that multiple consecutive replays do not increase transaction or event counts."""
    payload = {
        "event_id": "TEST_SEC_EVT_REPLAY_001",
        "event_type": "payment.captured",
        "payment_id": "TEST_SEC_PAY_REPLAY_001",
        "amount": 1800.00,
        "currency": "INR"
    }
    raw = json.dumps(payload).encode("utf-8")
    sig = generate_webhook_signature(raw, settings.WEBHOOK_SECRET)
    headers = {"Content-Type": "application/json", "X-Razorpay-Signature": sig}

    # Initial post
    r0 = client.post("/webhook/payment", content=raw, headers=headers)
    assert r0.status_code == 200

    # 4 consecutive replays
    for _ in range(4):
        r = client.post("/webhook/payment", content=raw, headers=headers)
        assert r.status_code == 409

    db = SessionLocal()
    try:
        assert db.query(WebhookEvent).filter_by(event_id="TEST_SEC_EVT_REPLAY_001").count() == 1
        assert db.query(Transaction).filter_by(transaction_id="TEST_SEC_PAY_REPLAY_001").count() == 1
        dup_audits = db.query(AuditLog).filter_by(
            action="WEBHOOK_DUPLICATE_REJECTED",
            entity_id="TEST_SEC_EVT_REPLAY_001"
        ).count()
        assert dup_audits == 4
    finally:
        db.close()
