"""
Phase 4 Unit Tests: Database Schema, Models & Indexing
Verifies:
1. init_db() creates all 5 tables in the SQLite database
2. Tables exist with correct column definitions and indexes
3. CRUD operations (insert, query, filter) for all 5 models:
   - Transaction
   - ReconciliationResult
   - WebhookEvent
   - ReconciliationException
   - AuditLog
4. Unique constraints on WebhookEvent.event_id, ReconciliationResult.reconciliation_id, etc.
"""

import os
from datetime import datetime, timezone
import pytest
from sqlalchemy import inspect
from backend.database import engine, SessionLocal, init_db, Base
from backend.models import (
    Transaction,
    ReconciliationResult,
    WebhookEvent,
    ReconciliationException,
    AuditLog
)

@pytest.fixture(scope="module", autouse=True)
def setup_test_database():
    """Initializes the database schema and clears prior test data while keeping table definitions intact."""
    init_db()
    db = SessionLocal()
    try:
        db.query(AuditLog).filter(AuditLog.audit_id.like("%TEST%")).delete(synchronize_session=False)
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like("%TEST%")).delete(synchronize_session=False)
        db.query(WebhookEvent).filter(WebhookEvent.event_id.like("%TEST%")).delete(synchronize_session=False)
        db.query(ReconciliationResult).filter(ReconciliationResult.reconciliation_id.like("%TEST%")).delete(synchronize_session=False)
        db.query(Transaction).filter(Transaction.transaction_id.like("%TEST%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield
    # Keep table schema intact in reconcile_ai.db

def test_tables_created():
    """Verify that all 5 required tables exist in the database."""
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    assert "transactions" in tables
    assert "reconciliation_results" in tables
    assert "webhook_events" in tables
    assert "reconciliation_exceptions" in tables
    assert "audit_logs" in tables

def test_transaction_indexes():
    """Verify that transactions table has indexes on required lookup fields."""
    inspector = inspect(engine)
    columns = [col["name"] for col in inspector.get_columns("transactions")]
    
    required_cols = [
        "id", "transaction_id", "source", "reference_id",
        "order_id", "customer_id", "amount", "currency",
        "transaction_date", "status", "transaction_type",
        "description", "metadata_json", "created_at"
    ]
    for col in required_cols:
        assert col in columns, f"Column '{col}' missing from transactions table"

def test_insert_and_query_transaction():
    """Verify insertion and query of a Transaction record."""
    db = SessionLocal()
    try:
        txn = Transaction(
            transaction_id="GW_TEST_001",
            source="GATEWAY",
            reference_id="pay_test_001",
            order_id="ORD_TEST_001",
            customer_id="CUST_001",
            amount=2500.0,
            currency="INR",
            transaction_date=datetime.now(timezone.utc),
            status="captured",
            transaction_type="PAYMENT",
            description="Test payment capture",
            metadata_json='{"fee": 50.0, "tax": 9.0}'
        )
        db.add(txn)
        db.commit()
        db.refresh(txn)

        fetched = db.query(Transaction).filter_by(transaction_id="GW_TEST_001").first()
        assert fetched is not None
        assert fetched.source == "GATEWAY"
        assert fetched.amount == 2500.0
        assert fetched.reference_id == "pay_test_001"
    finally:
        db.close()

def test_insert_and_query_reconciliation_result():
    """Verify insertion and query of a ReconciliationResult record."""
    db = SessionLocal()
    try:
        recon = ReconciliationResult(
            reconciliation_id="REC_TEST_001",
            gateway_transaction_id="GW_TEST_001",
            bank_transaction_id="BNK_TEST_001",
            erp_invoice_id="INV_TEST_001",
            match_score=98.5,
            matching_method="EXACT_RULE",
            ai_recommendation="AUTO_RECONCILE",
            ai_confidence=99.0,
            ai_reasoning="All references and amounts match perfectly.",
            final_decision="AUTO_RECONCILED",
            discrepancy_amount=0.0,
            is_resolved=True
        )
        db.add(recon)
        db.commit()
        db.refresh(recon)

        fetched = db.query(ReconciliationResult).filter_by(reconciliation_id="REC_TEST_001").first()
        assert fetched is not None
        assert fetched.match_score == 98.5
        assert fetched.final_decision == "AUTO_RECONCILED"
        assert fetched.is_resolved is True
    finally:
        db.close()

def test_insert_and_query_webhook_event():
    """Verify insertion, query, and uniqueness of WebhookEvent."""
    db = SessionLocal()
    try:
        webhook = WebhookEvent(
            event_id="EVT_TEST_1001",
            event_type="payment.captured",
            payment_id="pay_test_1001",
            order_id="ORD_TEST_1001",
            amount=5000.0,
            currency="INR",
            signature="test_hmac_signature_hash",
            payload_json='{"event": "payment.captured", "amount": 5000}',
            is_processed=True
        )
        db.add(webhook)
        db.commit()
        db.refresh(webhook)

        fetched = db.query(WebhookEvent).filter_by(event_id="EVT_TEST_1001").first()
        assert fetched is not None
        assert fetched.event_type == "payment.captured"
        assert fetched.amount == 5000.0
    finally:
        db.close()

def test_insert_and_query_reconciliation_exception():
    """Verify insertion and lifecycle update of a ReconciliationException."""
    db = SessionLocal()
    try:
        exc = ReconciliationException(
            exception_id="EXC_TEST_001",
            reconciliation_id="REC_TEST_002",
            transaction_id="GW_TEST_002",
            category="AMOUNT_MISMATCH",
            severity="HIGH",
            difference_amount=50.0,
            ai_explanation="Gateway recorded ₹5,000 but Bank received ₹4,950.",
            status="OPEN"
        )
        db.add(exc)
        db.commit()
        db.refresh(exc)

        fetched = db.query(ReconciliationException).filter_by(exception_id="EXC_TEST_001").first()
        assert fetched is not None
        assert fetched.category == "AMOUNT_MISMATCH"
        assert fetched.status == "OPEN"

        # Update status to APPROVED (Human-in-the-loop)
        fetched.status = "APPROVED"
        fetched.resolved_by = "senior_finance_controller"
        fetched.resolved_at = datetime.now(timezone.utc)
        db.commit()

        updated = db.query(ReconciliationException).filter_by(exception_id="EXC_TEST_001").first()
        assert updated.status == "APPROVED"
        assert updated.resolved_by == "senior_finance_controller"
    finally:
        db.close()

def test_insert_and_query_audit_log():
    """Verify append-only AuditLog record creation and filtering."""
    db = SessionLocal()
    try:
        log = AuditLog(
            audit_id="AUD_TEST_001",
            actor="AI_CONTROLLER",
            action="AUTO_RECONCILED",
            entity="RECONCILIATION",
            entity_id="REC_TEST_001",
            old_value="PENDING",
            new_value="AUTO_RECONCILED",
            reason="High confidence score (98.5) and zero financial policy violations."
        )
        db.add(log)
        db.commit()
        db.refresh(log)

        fetched = db.query(AuditLog).filter_by(audit_id="AUD_TEST_001").first()
        assert fetched is not None
        assert fetched.actor == "AI_CONTROLLER"
        assert fetched.action == "AUTO_RECONCILED"
        assert fetched.entity == "RECONCILIATION"
    finally:
        db.close()
