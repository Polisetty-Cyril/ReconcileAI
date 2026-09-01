"""
Phase 9 Unit & Integration Tests: Payment Webhook Simulator
Verifies:
1. Successful payment.authorized webhook ingestion & mapping (PAYMENT -> AUTHORIZED)
2. Successful payment.captured webhook ingestion & mapping (PAYMENT -> CAPTURED)
3. Successful payment.failed webhook ingestion & mapping (PAYMENT -> FAILED)
4. Successful refund.created webhook ingestion & mapping (REFUND -> REFUNDED)
5. Payload validation:
   - Malformed payloads (missing fields, wrong data types)
   - Negative / zero amounts
   - Unsupported / invalid event types
6. Database persistence:
   - WebhookEvent entity creation, idempotency key (event_id), and raw JSON storage
   - Canonical Transaction entity creation with source='GATEWAY'
   - AuditLog entity creation with actor='WEBHOOK_GATEWAY' and action='WEBHOOK_RECEIVED'
7. Proper field mapping:
   - reference_id -> payment_id
   - amount, currency ('INR')
   - fee, tax, net_amount in metadata_json
8. Integration with Phase 6 Deterministic Engine, Phase 7 Fuzzy Matcher, and Phase 8 AI Controller
"""

import os
import json
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.main import app
from backend.config import settings
from backend.database import SessionLocal, init_db
from backend.models import (
    Transaction,
    WebhookEvent,
    AuditLog,
    ReconciliationResult,
    ReconciliationException
)
from backend.services.webhook import WebhookSimulatorService
from backend.services.security import generate_webhook_signature
from backend.schemas.webhook import PaymentWebhookPayload
from backend.services.reconciliation import DeterministicReconciliationEngine
from backend.services.fuzzy_matcher import FuzzyMatchEngine
from backend.services.ai_controller import AIController

client = TestClient(app)

def post_signed_webhook(client, payload_dict):
    """Helper to post a signed webhook using settings.WEBHOOK_SECRET."""
    raw_body = json.dumps(payload_dict).encode("utf-8")
    sig = generate_webhook_signature(raw_body, settings.WEBHOOK_SECRET)
    return client.post(
        "/webhook/payment",
        content=raw_body,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": sig}
    )

@pytest.fixture(scope="module", autouse=True)
def setup_db():
    """Initializes schema and cleans up test data before and after tests."""
    init_db()
    db: Session = SessionLocal()
    try:
        db.query(AuditLog).filter(AuditLog.audit_id.like("%TEST_WH%")).delete(synchronize_session=False)
        db.query(WebhookEvent).filter(WebhookEvent.event_id.like("%TEST_EVT%")).delete(synchronize_session=False)
        db.query(Transaction).filter(Transaction.transaction_id.like("%TEST_PAY%")).delete(synchronize_session=False)
        db.query(Transaction).filter(Transaction.transaction_id.like("%TEST_RFND%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(AuditLog).filter(AuditLog.audit_id.like("%TEST_WH%")).delete(synchronize_session=False)
        db.query(WebhookEvent).filter(WebhookEvent.event_id.like("%TEST_EVT%")).delete(synchronize_session=False)
        db.query(Transaction).filter(Transaction.transaction_id.like("%TEST_PAY%")).delete(synchronize_session=False)
        db.query(Transaction).filter(Transaction.transaction_id.like("%TEST_RFND%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()

# -----------------------------------------------------------------------------
# 1. Successful Webhook Ingestion Tests via Endpoint & Service
# -----------------------------------------------------------------------------

def test_webhook_payment_authorized_success():
    """Verify successful ingestion of payment.authorized webhook."""
    payload = {
        "event_id": "TEST_EVT_AUTH_001",
        "event_type": "payment.authorized",
        "payment_id": "TEST_PAY_AUTH_001",
        "order_id": "ORD_AUTH_001",
        "customer_id": "CUST_001",
        "amount": 2500.00,
        "currency": "INR",
        "payment_method": "card",
        "fee": 50.00,
        "tax": 9.00,
        "description": "Authorized test payment",
        "timestamp": "2026-03-01T10:00:00Z"
    }

    response = post_signed_webhook(client, payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["event_id"] == "TEST_EVT_AUTH_001"
    assert data["transaction_id"] == "TEST_PAY_AUTH_001"
    assert data["event_type"] == "payment.authorized"
    assert data["processed"] is True

    # Verify DB persistence
    db = SessionLocal()
    try:
        wh_event = db.query(WebhookEvent).filter_by(event_id="TEST_EVT_AUTH_001").first()
        assert wh_event is not None
        assert wh_event.event_type == "payment.authorized"
        assert wh_event.payment_id == "TEST_PAY_AUTH_001"
        assert wh_event.amount == 2500.00
        assert wh_event.is_processed is True

        txn = db.query(Transaction).filter_by(transaction_id="TEST_PAY_AUTH_001", source="GATEWAY").first()
        assert txn is not None
        assert txn.source == "GATEWAY"
        assert txn.reference_id == "TEST_PAY_AUTH_001"
        assert txn.order_id == "ORD_AUTH_001"
        assert txn.customer_id == "CUST_001"
        assert txn.amount == 2500.00
        assert txn.currency == "INR"
        assert txn.status == "AUTHORIZED"
        assert txn.transaction_type == "PAYMENT"

        audit = db.query(AuditLog).filter_by(entity="WEBHOOK", entity_id="TEST_EVT_AUTH_001").first()
        assert audit is not None
        assert audit.actor == "WEBHOOK_GATEWAY"
        assert audit.action == "WEBHOOK_RECEIVED"
    finally:
        db.close()

def test_webhook_payment_captured_success():
    """Verify successful ingestion of payment.captured webhook."""
    payload = {
        "event_id": "TEST_EVT_CAP_002",
        "event_type": "payment.captured",
        "payment_id": "TEST_PAY_CAP_002",
        "order_id": "ORD_CAP_002",
        "customer_id": "CUST_002",
        "amount": 4999.50,
        "currency": "INR",
        "payment_method": "upi",
        "fee": 0.00,
        "tax": 0.00,
        "description": "Captured UPI payment"
    }

    response = post_signed_webhook(client, payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["event_type"] == "payment.captured"

    db = SessionLocal()
    try:
        txn = db.query(Transaction).filter_by(transaction_id="TEST_PAY_CAP_002", source="GATEWAY").first()
        assert txn is not None
        assert txn.status == "CAPTURED"
        assert txn.transaction_type == "PAYMENT"
        assert txn.amount == 4999.50
        meta = json.loads(txn.metadata_json)
        assert meta["payment_method"] == "upi"
        assert meta["net_amount"] == 4999.50
    finally:
        db.close()

def test_webhook_payment_failed_success():
    """Verify successful ingestion of payment.failed webhook."""
    payload = {
        "event_id": "TEST_EVT_FAIL_003",
        "event_type": "payment.failed",
        "payment_id": "TEST_PAY_FAIL_003",
        "order_id": "ORD_FAIL_003",
        "amount": 1200.00,
        "currency": "INR",
        "description": "Payment failed due to insufficient funds"
    }

    response = post_signed_webhook(client, payload)
    assert response.status_code == 200

    db = SessionLocal()
    try:
        txn = db.query(Transaction).filter_by(transaction_id="TEST_PAY_FAIL_003", source="GATEWAY").first()
        assert txn is not None
        assert txn.status == "FAILED"
        assert txn.transaction_type == "PAYMENT"
        assert txn.amount == 1200.00
    finally:
        db.close()

def test_webhook_refund_created_success():
    """Verify successful ingestion of refund.created webhook."""
    payload = {
        "event_id": "TEST_EVT_RFND_004",
        "event_type": "refund.created",
        "payment_id": "TEST_RFND_004",
        "order_id": "ORD_RFND_004",
        "amount": 750.00,
        "currency": "INR",
        "description": "Partial refund processed"
    }

    response = post_signed_webhook(client, payload)
    assert response.status_code == 200

    db = SessionLocal()
    try:
        txn = db.query(Transaction).filter_by(transaction_id="TEST_RFND_004", source="GATEWAY").first()
        assert txn is not None
        assert txn.status == "REFUNDED"
        assert txn.transaction_type == "REFUND"
        assert txn.amount == 750.00
    finally:
        db.close()

# -----------------------------------------------------------------------------
# 2. Payload Validation & Error Handling Tests
# -----------------------------------------------------------------------------

def test_webhook_invalid_event_type_rejected():
    """Verify that unsupported event types are rejected with HTTP 422 or 400."""
    payload = {
        "event_id": "TEST_EVT_INV_001",
        "event_type": "subscription.charged",  # Unsupported
        "payment_id": "TEST_PAY_INV_001",
        "amount": 500.00
    }
    response = post_signed_webhook(client, payload)
    assert response.status_code in (400, 422)

def test_webhook_missing_required_fields_rejected():
    """Verify that missing event_id or payment_id triggers validation error."""
    # Missing payment_id
    payload1 = {
        "event_id": "TEST_EVT_INV_002",
        "event_type": "payment.captured",
        "amount": 500.00
    }
    response1 = post_signed_webhook(client, payload1)
    assert response1.status_code == 422

    # Missing event_id
    payload2 = {
        "event_type": "payment.captured",
        "payment_id": "TEST_PAY_INV_002",
        "amount": 500.00
    }
    response2 = post_signed_webhook(client, payload2)
    assert response2.status_code == 422

def test_webhook_negative_or_zero_amount_rejected():
    """Verify that negative or zero amount is rejected."""
    payload_zero = {
        "event_id": "TEST_EVT_INV_003",
        "event_type": "payment.captured",
        "payment_id": "TEST_PAY_INV_003",
        "amount": 0.00
    }
    response_zero = post_signed_webhook(client, payload_zero)
    assert response_zero.status_code in (400, 422)

    payload_neg = {
        "event_id": "TEST_EVT_INV_004",
        "event_type": "payment.captured",
        "payment_id": "TEST_PAY_INV_004",
        "amount": -100.00
    }
    response_neg = post_signed_webhook(client, payload_neg)
    assert response_neg.status_code in (400, 422)

def test_webhook_malformed_json_rejected():
    """Verify that malformed body returns HTTP 422."""
    raw_body = b"not-json-content"
    sig = generate_webhook_signature(raw_body, settings.WEBHOOK_SECRET)
    response = client.post(
        "/webhook/payment",
        content=raw_body,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": sig}
    )
    assert response.status_code == 422

# -----------------------------------------------------------------------------
# 3. Direct Service Unit Tests & State Transitions
# -----------------------------------------------------------------------------

def test_webhook_service_state_update_on_subsequent_event():
    """Verify that an authorized payment subsequently receiving captured event updates the transaction status."""
    db = SessionLocal()
    try:
        # Step 1: Authorized event
        auth_payload = PaymentWebhookPayload(
            event_id="TEST_EVT_SEQ_001",
            event_type="payment.authorized",
            payment_id="TEST_PAY_SEQ_001",
            order_id="ORD_SEQ_001",
            amount=3000.00,
            currency="INR"
        )
        res1 = WebhookSimulatorService.process_webhook(db, auth_payload)
        assert res1["status"] == "success"

        txn1 = db.query(Transaction).filter_by(transaction_id="TEST_PAY_SEQ_001", source="GATEWAY").first()
        assert txn1.status == "AUTHORIZED"

        # Step 2: Captured event for the same payment (new unique event_id)
        cap_payload = PaymentWebhookPayload(
            event_id="TEST_EVT_SEQ_002",
            event_type="payment.captured",
            payment_id="TEST_PAY_SEQ_001",
            order_id="ORD_SEQ_001",
            amount=3000.00,
            currency="INR"
        )
        res2 = WebhookSimulatorService.process_webhook(db, cap_payload)
        assert res2["status"] == "success"

        txn2 = db.query(Transaction).filter_by(transaction_id="TEST_PAY_SEQ_001", source="GATEWAY").first()
        assert txn2.status == "CAPTURED"
        assert txn2.amount == 3000.00

        # Both webhook events should be recorded
        evts = db.query(WebhookEvent).filter(WebhookEvent.payment_id == "TEST_PAY_SEQ_001").all()
        assert len(evts) == 2
    finally:
        db.close()

# -----------------------------------------------------------------------------
# 4. Pipeline Integration Tests (Phase 6, Phase 7, Phase 8 Compatibility)
# -----------------------------------------------------------------------------

def test_webhook_transaction_integrates_with_phase6_deterministic_reconciliation():
    """Verify that a webhook-created gateway transaction successfully reconciles with Phase 6 deterministic engine."""
    db = SessionLocal()
    try:
        # Ingest webhook
        payload = {
            "event_id": "TEST_EVT_RECON_001",
            "event_type": "payment.captured",
            "payment_id": "TEST_PAY_RECON_001",
            "order_id": "ORD_RECON_001",
            "amount": 1500.00,
            "currency": "INR",
            "timestamp": "2026-03-01T12:00:00Z"
        }
        res = post_signed_webhook(client, payload)
        assert res.status_code == 200

        gw_txn = db.query(Transaction).filter_by(transaction_id="TEST_PAY_RECON_001").first()
        assert gw_txn is not None

        # Create matching Bank and ERP transactions
        bank_txn = Transaction(
            transaction_id="TEST_BNK_RECON_001",
            source="BANK",
            reference_id="TEST_PAY_RECON_001",
            order_id="ORD_RECON_001",
            amount=1500.00,
            currency="INR",
            transaction_date=datetime(2026, 3, 1, 12, 30, tzinfo=timezone.utc),
            status="CREDIT",
            transaction_type="SETTLEMENT",
            description="Bank settlement for TEST_PAY_RECON_001"
        )
        erp_txn = Transaction(
            transaction_id="TEST_INV_RECON_001",
            source="ERP",
            reference_id="TEST_PAY_RECON_001",
            order_id="ORD_RECON_001",
            amount=1500.00,
            currency="INR",
            transaction_date=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
            status="PAID",
            transaction_type="INVOICE",
            description="ERP Invoice for ORD_RECON_001"
        )
        db.add(bank_txn)
        db.add(erp_txn)
        db.commit()

        # Run Phase 6 Deterministic Engine
        engine = DeterministicReconciliationEngine(amount_tolerance=0.0, date_tolerance_days=3)
        summary = engine.reconcile_transactions([gw_txn, bank_txn, erp_txn])

        assert summary["total_clusters"] == 1
        assert summary["total_reconciled"] == 1
        assert len(summary["results"]) == 1
        
        recon_result = summary["results"][0]
        assert recon_result.final_decision == "AUTO_RECONCILED"
        assert recon_result.match_score == 100.0
        assert recon_result.gateway_transaction_id == "TEST_PAY_RECON_001"

        db.delete(bank_txn)
        db.delete(erp_txn)
        db.commit()
    finally:
        db.close()

def test_webhook_transaction_integrates_with_phase7_fuzzy_matcher():
    """Verify that a webhook-created transaction integrates with Phase 7 fuzzy matcher."""
    db = SessionLocal()
    try:
        # Ingest webhook with slight discrepancy in order reference
        payload = {
            "event_id": "TEST_EVT_FUZZY_001",
            "event_type": "payment.captured",
            "payment_id": "TEST_PAY_FUZZY_001",
            "order_id": "ORD_FUZZY_1001",
            "customer_id": "Acme Corp",
            "amount": 2800.00,
            "currency": "INR",
            "description": "Payment for Acme Corp Invoice",
            "timestamp": "2026-03-01T14:00:00Z"
        }
        res = post_signed_webhook(client, payload)
        assert res.status_code == 200

        gw_txn = db.query(Transaction).filter_by(transaction_id="TEST_PAY_FUZZY_001").first()
        
        # Bank record has typo in reference and matching description
        bank_txn = Transaction(
            transaction_id="TEST_BNK_FUZZY_001",
            source="BANK",
            reference_id="TEST_PAY_FUZZY_001_A",
            order_id="ORD_FUZZY_1001",
            customer_id="Acme Corp",
            amount=2800.00,
            currency="INR",
            transaction_date=datetime(2026, 3, 1, 15, 0, tzinfo=timezone.utc),
            status="CREDIT",
            transaction_type="SETTLEMENT",
            description="Payment for Acme Corp Invoice settlement"
        )
        db.add(bank_txn)
        db.commit()

        matcher = FuzzyMatchEngine()
        result = matcher.score_pair(gw_txn, bank_txn)
        assert result.composite_score >= 70.0
        assert result.amount_match is True
        assert result.reference_score > 80.0

        db.delete(bank_txn)
        db.commit()
    finally:
        db.close()

def test_webhook_transaction_integrates_with_phase8_ai_controller():
    """Verify that Phase 8 AI controller can evaluate webhook transactions."""
    controller = AIController()
    
    recon_result = ReconciliationResult(
        reconciliation_id="TEST_RECON_WH_001",
        gateway_transaction_id="TEST_PAY_CAP_002",
        match_score=50.0,
        matching_method="RULE_BASED",
        final_decision="HUMAN_REVIEW",
        discrepancy_amount=3500.00
    )

    # Create exception payload from a webhook transaction discrepancy
    exception_record = ReconciliationException(
        exception_id="TEST_EXC_WH_001",
        reconciliation_id="TEST_RECON_WH_001",
        transaction_id="TEST_PAY_CAP_002",
        category="MISSING_BANK_TRANSACTION",
        severity="MEDIUM",
        difference_amount=3500.00,
        status="OPEN"
    )

    decision = controller.investigate(
        result=recon_result,
        exception=exception_record
    )
    assert decision is not None
    assert decision.recommendation in ("APPROVE", "REJECT", "REVIEW", "ESCALATE")
    assert decision.confidence >= 0.0
    assert decision.risk in ("LOW", "MEDIUM", "HIGH")
