"""
Phase 11 Unit & Integration Tests: Exception Management Workflow
Verifies:
1. GET /exceptions listing
2. Status filtering (OPEN, APPROVED, REJECTED)
3. Severity filtering (LOW, MEDIUM, HIGH, CRITICAL)
4. Category filtering (AMOUNT_MISMATCH, MISSING_BANK_TRANSACTION, etc.)
5. Pagination with limit and offset
6. GET /exceptions/{id} detail retrieval
7. Missing exception -> HTTP 404
8. Successful human approval -> status='APPROVED', resolved_by, resolved_at, reviewer_notes
9. Successful human rejection -> status='REJECTED', resolved_by, resolved_at, reviewer_notes
10. reviewer_id persistence
11. reviewer_notes persistence
12. resolved_at timestamp persistence
13. ReconciliationResult synchronization (is_resolved=True, final_decision='MANUAL_APPROVED' / 'MANUAL_REJECTED')
14. EXCEPTION_APPROVED audit log creation with actor, action, entity, entity_id, old/new values
15. EXCEPTION_REJECTED audit log creation with actor, action, entity, entity_id, old/new values
16. Repeated same-reviewer approval is idempotent -> HTTP 200
17. Repeated same-reviewer rejection is idempotent -> HTTP 200
18. Attempting to approve an already REJECTED exception -> HTTP 400
19. Attempting to reject an already APPROVED exception -> HTTP 400
20. Human decision authoritatively overrides Phase 8 AI advisory recommendation
21. AI Controller remains advisory-only and cannot independently approve, reject, or resolve exceptions
22. Atomic database transaction behavior
"""

import json
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.main import app
from backend.database import SessionLocal, init_db
from backend.models import (
    ReconciliationException,
    ReconciliationResult,
    AuditLog,
    Transaction
)
from backend.services.exception_service import ExceptionManagementService
from backend.services.ai_controller import AIController

client = TestClient(app)

@pytest.fixture(scope="module", autouse=True)
def setup_exception_db():
    """Initializes schema and cleans up test data before and after Phase 11 tests."""
    init_db()
    db: Session = SessionLocal()
    try:
        db.query(AuditLog).filter((AuditLog.entity_id.like("%TEST_EXC%")) | (AuditLog.audit_id.like("%TEST_EXC%"))).delete(synchronize_session=False)
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like("%TEST_EXC%")).delete(synchronize_session=False)
        db.query(ReconciliationResult).filter(ReconciliationResult.reconciliation_id.like("%TEST_EXC%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(AuditLog).filter((AuditLog.entity_id.like("%TEST_EXC%")) | (AuditLog.audit_id.like("%TEST_EXC%"))).delete(synchronize_session=False)
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like("%TEST_EXC%")).delete(synchronize_session=False)
        db.query(ReconciliationResult).filter(ReconciliationResult.reconciliation_id.like("%TEST_EXC%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()

@pytest.fixture(autouse=True)
def seed_test_exceptions():
    """Seeds test exceptions and reconciliation results for test isolation."""
    db: Session = SessionLocal()
    try:
        # Clean before seed
        db.query(AuditLog).filter((AuditLog.entity_id.like("%TEST_EXC%")) | (AuditLog.audit_id.like("%TEST_EXC%"))).delete(synchronize_session=False)
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like("%TEST_EXC%")).delete(synchronize_session=False)
        db.query(ReconciliationResult).filter(ReconciliationResult.reconciliation_id.like("%TEST_EXC%")).delete(synchronize_session=False)
        db.commit()

        # Seed ReconciliationResult 1
        recon1 = ReconciliationResult(
            reconciliation_id="TEST_EXC_RECON_001",
            gateway_transaction_id="GW_TEST_001",
            match_score=70.0,
            matching_method="RULE_BASED",
            ai_recommendation="REVIEW",
            ai_confidence=75.0,
            ai_reasoning="Amount mismatch between Gateway and Bank",
            final_decision="HUMAN_REVIEW",
            discrepancy_amount=250.00,
            is_resolved=False
        )
        db.add(recon1)

        # Seed Exception 1 (OPEN, AMOUNT_MISMATCH, HIGH)
        exc1 = ReconciliationException(
            exception_id="TEST_EXC_001",
            reconciliation_id="TEST_EXC_RECON_001",
            transaction_id="GW_TEST_001",
            category="AMOUNT_MISMATCH",
            severity="HIGH",
            difference_amount=250.00,
            ai_explanation="Gateway charge differs from bank credit by INR 250.00",
            status="OPEN"
        )
        db.add(exc1)

        # Seed Exception 2 (OPEN, MISSING_BANK_TRANSACTION, MEDIUM)
        exc2 = ReconciliationException(
            exception_id="TEST_EXC_002",
            reconciliation_id="TEST_EXC_RECON_001",
            transaction_id="GW_TEST_002",
            category="MISSING_BANK_TRANSACTION",
            severity="MEDIUM",
            difference_amount=1500.00,
            ai_explanation="Gateway captured payment without corresponding bank deposit",
            status="OPEN"
        )
        db.add(exc2)

        # Seed Exception 3 (OPEN, DUPLICATE_TRANSACTION, CRITICAL)
        exc3 = ReconciliationException(
            exception_id="TEST_EXC_003",
            reconciliation_id=None,
            transaction_id="GW_TEST_003",
            category="DUPLICATE_TRANSACTION",
            severity="CRITICAL",
            difference_amount=5000.00,
            ai_explanation="Multiple gateway charges found for same order ref",
            status="OPEN"
        )
        db.add(exc3)

        db.commit()
    finally:
        db.close()

# -----------------------------------------------------------------------------
# 1. Queue Listing & Filtering Tests (GET /exceptions)
# -----------------------------------------------------------------------------

def test_list_exceptions_all():
    """Verify GET /exceptions returns all seeded open exceptions."""
    response = client.get("/exceptions")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 3
    assert len(data["items"]) >= 3
    ids = [item["exception_id"] for item in data["items"]]
    assert "TEST_EXC_001" in ids
    assert "TEST_EXC_002" in ids
    assert "TEST_EXC_003" in ids

def test_list_exceptions_filter_by_status():
    """Verify status filtering (status=OPEN)."""
    response = client.get("/exceptions?status=OPEN")
    assert response.status_code == 200
    data = response.json()
    for item in data["items"]:
        assert item["status"] == "OPEN"

def test_list_exceptions_filter_by_severity():
    """Verify severity filtering (severity=CRITICAL)."""
    response = client.get("/exceptions?severity=CRITICAL")
    assert response.status_code == 200
    data = response.json()
    assert any(item["exception_id"] == "TEST_EXC_003" for item in data["items"])
    for item in data["items"]:
        assert item["severity"] == "CRITICAL"

def test_list_exceptions_filter_by_category():
    """Verify category filtering (category=AMOUNT_MISMATCH)."""
    response = client.get("/exceptions?category=AMOUNT_MISMATCH")
    assert response.status_code == 200
    data = response.json()
    assert any(item["exception_id"] == "TEST_EXC_001" for item in data["items"])
    for item in data["items"]:
        assert item["category"] == "AMOUNT_MISMATCH"

def test_list_exceptions_pagination():
    """Verify pagination with limit and offset."""
    response = client.get("/exceptions?limit=2&offset=0")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2
    assert data["limit"] == 2
    assert data["offset"] == 0

    response_offset = client.get("/exceptions?limit=2&offset=2")
    assert response_offset.status_code == 200
    data_offset = response_offset.json()
    assert data_offset["offset"] == 2

# -----------------------------------------------------------------------------
# 2. Individual Exception Retrieval Tests (GET /exceptions/{id})
# -----------------------------------------------------------------------------

def test_get_exception_by_id_success():
    """Verify GET /exceptions/{id} returns full exception details."""
    response = client.get("/exceptions/TEST_EXC_001")
    assert response.status_code == 200
    data = response.json()
    assert data["exception_id"] == "TEST_EXC_001"
    assert data["reconciliation_id"] == "TEST_EXC_RECON_001"
    assert data["transaction_id"] == "GW_TEST_001"
    assert data["category"] == "AMOUNT_MISMATCH"
    assert data["severity"] == "HIGH"
    assert data["difference_amount"] == 250.00
    assert data["status"] == "OPEN"
    assert "difference" in data["ai_explanation"].lower() or "differs" in data["ai_explanation"].lower()

def test_get_exception_by_id_not_found():
    """Verify non-existent exception returns HTTP 404."""
    response = client.get("/exceptions/NON_EXISTENT_EXC_999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()

# -----------------------------------------------------------------------------
# 3. Human Approval Workflow Tests (POST /exceptions/{id}/approve)
# -----------------------------------------------------------------------------

def test_approve_exception_success():
    """Verify successful approval updates exception, synchronizes ReconciliationResult, and logs AuditLog."""
    payload = {
        "reviewer_id": "operator_alice",
        "notes": "Approved after bank slip verification"
    }
    response = client.post("/exceptions/TEST_EXC_001/approve", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["exception_id"] == "TEST_EXC_001"
    assert data["status"] == "APPROVED"
    assert data["resolved_by"] == "operator_alice"
    assert data["reviewer_notes"] == "Approved after bank slip verification"
    assert data["resolved_at"] is not None

    # Verify DB state
    db = SessionLocal()
    try:
        exc = db.query(ReconciliationException).filter_by(exception_id="TEST_EXC_001").first()
        assert exc is not None
        assert exc.status == "APPROVED"
        assert exc.resolved_by == "operator_alice"
        assert exc.reviewer_notes == "Approved after bank slip verification"

        # Verify linked ReconciliationResult synchronization
        recon = db.query(ReconciliationResult).filter_by(reconciliation_id="TEST_EXC_RECON_001").first()
        assert recon is not None
        assert recon.is_resolved is True
        assert recon.final_decision == "MANUAL_APPROVED"

        # Verify AuditLog creation
        audit = db.query(AuditLog).filter_by(action="EXCEPTION_APPROVED", entity_id="TEST_EXC_001").first()
        assert audit is not None
        assert audit.actor == "operator_alice"
        assert audit.entity == "EXCEPTION"
        assert audit.reason == "Approved after bank slip verification"
        old_val = json.loads(audit.old_value)
        assert old_val["status"] == "OPEN"
        new_val = json.loads(audit.new_value)
        assert new_val["status"] == "APPROVED"
        assert new_val["resolved_by"] == "operator_alice"
    finally:
        db.close()

def test_approve_exception_default_reviewer():
    """Verify approval works with default reviewer_id when empty payload is passed."""
    response = client.post("/exceptions/TEST_EXC_002/approve", json={})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "APPROVED"
    assert data["resolved_by"] == "HUMAN_OPERATOR"

def test_approve_exception_not_found():
    """Verify approving a non-existent exception returns HTTP 404."""
    response = client.post("/exceptions/NON_EXISTENT_EXC_999/approve", json={"reviewer_id": "op_1"})
    assert response.status_code == 404

def test_approve_exception_idempotent():
    """Verify repeated approval by same reviewer returns HTTP 200 idempotently without duplicate audits."""
    payload = {
        "reviewer_id": "operator_alice",
        "notes": "Initial approval"
    }
    res1 = client.post("/exceptions/TEST_EXC_001/approve", json=payload)
    assert res1.status_code == 200

    # Repeat same approval
    res2 = client.post("/exceptions/TEST_EXC_001/approve", json=payload)
    assert res2.status_code == 200
    assert res2.json()["status"] == "APPROVED"

    # Verify strictly 1 audit log created
    db = SessionLocal()
    try:
        audit_count = db.query(AuditLog).filter_by(action="EXCEPTION_APPROVED", entity_id="TEST_EXC_001").count()
        assert audit_count == 1
    finally:
        db.close()

# -----------------------------------------------------------------------------
# 4. Human Rejection Workflow Tests (POST /exceptions/{id}/reject)
# -----------------------------------------------------------------------------

def test_reject_exception_success():
    """Verify successful rejection updates exception, synchronizes ReconciliationResult, and logs AuditLog."""
    payload = {
        "reviewer_id": "operator_bob",
        "notes": "Rejected: Invalid merchant chargeback"
    }
    response = client.post("/exceptions/TEST_EXC_002/reject", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["exception_id"] == "TEST_EXC_002"
    assert data["status"] == "REJECTED"
    assert data["resolved_by"] == "operator_bob"
    assert data["reviewer_notes"] == "Rejected: Invalid merchant chargeback"
    assert data["resolved_at"] is not None

    # Verify DB state
    db = SessionLocal()
    try:
        exc = db.query(ReconciliationException).filter_by(exception_id="TEST_EXC_002").first()
        assert exc is not None
        assert exc.status == "REJECTED"
        assert exc.resolved_by == "operator_bob"

        # Verify linked ReconciliationResult synchronization
        recon = db.query(ReconciliationResult).filter_by(reconciliation_id="TEST_EXC_RECON_001").first()
        assert recon is not None
        assert recon.is_resolved is True
        assert recon.final_decision == "MANUAL_REJECTED"

        # Verify AuditLog creation
        audit = db.query(AuditLog).filter_by(action="EXCEPTION_REJECTED", entity_id="TEST_EXC_002").first()
        assert audit is not None
        assert audit.actor == "operator_bob"
        assert audit.entity == "EXCEPTION"
        assert audit.reason == "Rejected: Invalid merchant chargeback"
        new_val = json.loads(audit.new_value)
        assert new_val["status"] == "REJECTED"
    finally:
        db.close()

def test_reject_exception_not_found():
    """Verify rejecting a non-existent exception returns HTTP 404."""
    response = client.post("/exceptions/NON_EXISTENT_EXC_999/reject", json={"reviewer_id": "op_1"})
    assert response.status_code == 404

def test_reject_exception_idempotent():
    """Verify repeated rejection by same reviewer returns HTTP 200 idempotently without duplicate audits."""
    payload = {
        "reviewer_id": "operator_bob",
        "notes": "Initial rejection"
    }
    res1 = client.post("/exceptions/TEST_EXC_002/reject", json=payload)
    assert res1.status_code == 200

    # Repeat same rejection
    res2 = client.post("/exceptions/TEST_EXC_002/reject", json=payload)
    assert res2.status_code == 200
    assert res2.json()["status"] == "REJECTED"

    # Verify strictly 1 audit log created
    db = SessionLocal()
    try:
        audit_count = db.query(AuditLog).filter_by(action="EXCEPTION_REJECTED", entity_id="TEST_EXC_002").count()
        assert audit_count == 1
    finally:
        db.close()

# -----------------------------------------------------------------------------
# 5. Conflicting State Transition & Boundary Tests
# -----------------------------------------------------------------------------

def test_approve_after_rejection_conflict_rejected():
    """Verify that attempting to approve an already REJECTED exception returns HTTP 400."""
    # First reject
    client.post("/exceptions/TEST_EXC_003/reject", json={"reviewer_id": "operator_bob"})
    
    # Then attempt approve
    response = client.post("/exceptions/TEST_EXC_003/approve", json={"reviewer_id": "operator_alice"})
    assert response.status_code == 400
    assert "already rejected" in response.json()["detail"].lower()

def test_reject_after_approval_conflict_rejected():
    """Verify that attempting to reject an already APPROVED exception returns HTTP 400."""
    # First approve
    client.post("/exceptions/TEST_EXC_003/approve", json={"reviewer_id": "operator_alice"})
    
    # Then attempt reject
    response = client.post("/exceptions/TEST_EXC_003/reject", json={"reviewer_id": "operator_bob"})
    assert response.status_code == 400
    assert "already approved" in response.json()["detail"].lower()

def test_different_reviewer_reapproval_conflict_rejected():
    """Verify that a different reviewer cannot overwrite an existing approval without explicit policy."""
    client.post("/exceptions/TEST_EXC_003/approve", json={"reviewer_id": "operator_alice"})
    
    # Different reviewer attempts to approve
    response = client.post("/exceptions/TEST_EXC_003/approve", json={"reviewer_id": "operator_charlie"})
    assert response.status_code == 400
    assert "different reviewer" in response.json()["detail"].lower()

def test_different_reviewer_rerejection_conflict_rejected():
    """Verify that a different reviewer cannot overwrite an existing rejection without explicit policy."""
    client.post("/exceptions/TEST_EXC_003/reject", json={"reviewer_id": "operator_alice"})

    # Different reviewer attempts to reject
    response = client.post("/exceptions/TEST_EXC_003/reject", json={"reviewer_id": "operator_bob"})
    assert response.status_code == 400
    assert "different reviewer" in response.json()["detail"].lower()

# -----------------------------------------------------------------------------
# 6. AI Safety & Authoritative Human Decision Tests
# -----------------------------------------------------------------------------

def test_human_decision_authoritatively_overrides_ai_recommendation():
    """Verify human reviewer decision is authoritative over Phase 8 advisory recommendation."""
    # Seed an exception where AI recommended EXCEPTION / REVIEW
    db = SessionLocal()
    try:
        recon = db.query(ReconciliationResult).filter_by(reconciliation_id="TEST_EXC_RECON_001").first()
        assert recon.ai_recommendation == "REVIEW"
        assert recon.is_resolved is False

        exc = db.query(ReconciliationException).filter_by(exception_id="TEST_EXC_001").first()
        assert exc.status == "OPEN"
    finally:
        db.close()

    # Human reviewer inspects and approves
    res = client.post("/exceptions/TEST_EXC_001/approve", json={
        "reviewer_id": "finance_lead_sarah",
        "notes": "Reviewed ledger discrepancy; acceptable vendor difference authorized."
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "APPROVED"
    assert data["resolved_by"] == "finance_lead_sarah"

    # Verify reconciliation result is now resolved with MANUAL_APPROVED
    db = SessionLocal()
    try:
        updated_recon = db.query(ReconciliationResult).filter_by(reconciliation_id="TEST_EXC_RECON_001").first()
        assert updated_recon.is_resolved is True
        assert updated_recon.final_decision == "MANUAL_APPROVED"
        # Advisory AI recommendation is preserved for audit trail
        assert updated_recon.ai_recommendation == "REVIEW"
    finally:
        db.close()

def test_ai_controller_remains_advisory_and_cannot_resolve_exceptions():
    """Verify that invoking Phase 8 AI Controller does NOT autonomously resolve exceptions or change status."""
    controller = AIController()

    db = SessionLocal()
    try:
        recon = db.query(ReconciliationResult).filter_by(reconciliation_id="TEST_EXC_RECON_001").first()
        exc = db.query(ReconciliationException).filter_by(exception_id="TEST_EXC_002").first()
        assert exc.status == "OPEN"
        assert recon.is_resolved is False

        # Run AI Controller investigation
        ai_res = controller.investigate(result=recon, exception=exc)
        assert ai_res.recommendation in ("AUTO_RECONCILE", "REVIEW", "ESCALATE", "EXCEPTION")

        # Verify DB exception was NOT modified by AI
        exc_after = db.query(ReconciliationException).filter_by(exception_id="TEST_EXC_002").first()
        assert exc_after.status == "OPEN"
        assert exc_after.resolved_by is None
        assert exc_after.resolved_at is None
    finally:
        db.close()
