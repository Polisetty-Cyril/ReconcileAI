"""
ReconcileAI - Phase 14 FastAPI Backend Integration Tests
Verifies the complete Phase 14 API surface:
- GET /transactions (pagination, filtering, empty results)
- POST /transactions/load-synthetic (data loading, deduplication, error cases)
- POST /reconcile (reconciliation execution, duplicate-run safety, AI advisory preservation)
- GET /reconciliation/results (listing, pagination, filtering)
- GET /audit (immutable audit trail inspection, filtering, read-only enforcement)
- GET /reports/summary (real-time operational metrics, value at risk, category breakdowns)
- Query parameter validation and error handling
"""

import os
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.main import app
from backend.database import SessionLocal, init_db
from backend.models import (
    Transaction,
    ReconciliationResult,
    ReconciliationException,
    AuditLog,
)

client = TestClient(app)

PREFIX = "API14_"


@pytest.fixture(scope="module", autouse=True)
def force_offline_heuristic():
    """Forces heuristic LLM client during API tests to prevent external Gemini network calls."""
    from backend.config import settings
    orig_provider = settings.LLM_PROVIDER
    orig_key = settings.GEMINI_API_KEY
    settings.LLM_PROVIDER = "heuristic"
    settings.GEMINI_API_KEY = ""
    yield
    settings.LLM_PROVIDER = orig_provider
    settings.GEMINI_API_KEY = orig_key


@pytest.fixture(scope="module", autouse=True)
def setup_api_phase14_db(force_offline_heuristic):
    """Initializes schema and cleans up API test records before and after testing."""
    init_db()
    db: Session = SessionLocal()
    try:
        db.query(AuditLog).delete(synchronize_session=False)
        db.query(ReconciliationException).delete(synchronize_session=False)
        db.query(ReconciliationResult).delete(synchronize_session=False)
        db.query(Transaction).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()

    yield

    db = SessionLocal()
    try:
        db.query(AuditLog).delete(synchronize_session=False)
        db.query(ReconciliationException).delete(synchronize_session=False)
        db.query(ReconciliationResult).delete(synchronize_session=False)
        db.query(Transaction).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


# =============================================================================
# 1. Transaction API Tests
# =============================================================================

def test_get_transactions_empty():
    """Verifies GET /transactions returns a valid paginated response."""
    response = client.get("/transactions?source=NONEXISTENT_SOURCE_12345")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "items" in data
    assert data["total"] == 0
    assert data["items"] == []


def test_reconcile_empty_database_raises_400():
    """Verifies POST /reconcile fails cleanly if no transactions are in the database."""
    response = client.post("/reconcile")
    assert response.status_code == 400
    assert "no transactions found" in response.json()["detail"].lower()


def test_load_synthetic_transactions_invalid_dir():
    """Verifies POST /transactions/load-synthetic returns 404 for invalid data dir."""
    response = client.post("/transactions/load-synthetic?data_dir=invalid_nonexistent_dir_999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_load_synthetic_transactions_success():
    """Verifies POST /transactions/load-synthetic ingests primary data files."""
    response = client.post("/transactions/load-synthetic?data_dir=data&is_held_out=false")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCESS"
    assert data["gateway_loaded"] == 100
    assert data["bank_loaded"] == 89
    assert data["erp_loaded"] == 100
    assert data["total_loaded"] == 289
    assert "Successfully ingested" in data["message"]


def test_load_synthetic_transactions_idempotent():
    """Verifies repeated calls to load synthetic data safely update records without duplication."""
    response = client.post("/transactions/load-synthetic?data_dir=data&is_held_out=false")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCESS"
    assert data["total_loaded"] == 289


def test_get_transactions_filters_and_pagination():
    """Verifies GET /transactions supports source, status, and pagination parameters."""
    # Filter by source: GATEWAY
    resp_gw = client.get("/transactions?source=GATEWAY&limit=10&offset=0")
    assert resp_gw.status_code == 200
    data_gw = resp_gw.json()
    assert data_gw["total"] >= 100
    assert len(data_gw["items"]) == 10
    assert all(t["source"] == "GATEWAY" for t in data_gw["items"])

    # Filter by source: BANK
    resp_bnk = client.get("/transactions?source=BANK&limit=5&offset=5")
    assert resp_bnk.status_code == 200
    data_bnk = resp_bnk.json()
    assert data_bnk["total"] >= 89
    assert len(data_bnk["items"]) == 5
    assert all(t["source"] == "BANK" for t in data_bnk["items"])

    # Filter by status: captured
    resp_status = client.get("/transactions?status=captured&limit=20")
    assert resp_status.status_code == 200
    data_status = resp_status.json()
    assert data_status["total"] > 0
    assert all(t["status"] == "CAPTURED" for t in data_status["items"])


def test_get_transactions_date_filter():
    """Verifies GET /transactions filtering by start_date and end_date."""
    resp = client.get("/transactions?start_date=2026-08-01T00:00:00Z&end_date=2026-08-31T23:59:59Z&limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] > 0
    assert len(data["items"]) <= 10


# =============================================================================
# 2. Reconciliation API Tests
# =============================================================================

def test_reconcile_execution_success():
    """Verifies POST /reconcile executes the complete multi-source reconciliation pipeline."""
    response = client.post("/reconcile")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("COMPLETED", "SKIPPED")
    assert data["total_clusters"] >= 100
    assert data["total_reconciled"] >= 55
    assert data["auto_reconciled_rate"] >= 50.0
    assert data["unresolved_value_at_risk"] > 0.0
    assert "message" in data


def test_reconcile_duplicate_run_safety():
    """
    CRITICAL: Verifies that calling POST /reconcile again on already-reconciled data
    reports status="SKIPPED" without duplicating ReconciliationResult or Exception rows.
    """
    db = SessionLocal()
    try:
        initial_results_count = db.query(ReconciliationResult).count()
        initial_exceptions_count = db.query(ReconciliationException).count()
        initial_audit_count = db.query(AuditLog).count()
    finally:
        db.close()

    # Second call
    response = client.post("/reconcile")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SKIPPED"
    assert "already been reconciled" in data["message"].lower()

    # Confirm database row counts did NOT inflate
    db = SessionLocal()
    try:
        new_results_count = db.query(ReconciliationResult).count()
        new_exceptions_count = db.query(ReconciliationException).count()
        assert new_results_count == initial_results_count
        assert new_exceptions_count == initial_exceptions_count
    finally:
        db.close()


def test_reconciliation_results_listing():
    """Verifies GET /reconciliation/results lists paginated results."""
    response = client.get("/reconciliation/results?limit=15&offset=0")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 100
    assert len(data["items"]) == 15

    first_item = data["items"][0]
    assert "reconciliation_id" in first_item
    assert "match_score" in first_item
    assert "final_decision" in first_item
    assert "is_resolved" in first_item


def test_reconciliation_results_filters():
    """Verifies GET /reconciliation/results filterable by final_decision and is_resolved."""
    # Filter by AUTO_RECONCILED
    resp_auto = client.get("/reconciliation/results?final_decision=AUTO_RECONCILED&limit=10")
    assert resp_auto.status_code == 200
    data_auto = resp_auto.json()
    assert data_auto["total"] >= 55
    assert all(r["final_decision"] == "AUTO_RECONCILED" for r in data_auto["items"])

    # Filter by HUMAN_REVIEW
    resp_review = client.get("/reconciliation/results?final_decision=HUMAN_REVIEW&limit=10")
    assert resp_review.status_code == 200
    data_review = resp_review.json()
    assert data_review["total"] > 0
    assert all(r["final_decision"] == "HUMAN_REVIEW" for r in data_review["items"])

    # Filter by is_resolved=False
    resp_unresolved = client.get("/reconciliation/results?is_resolved=false&limit=10")
    assert resp_unresolved.status_code == 200
    data_unres = resp_unresolved.json()
    assert all(r["is_resolved"] is False for r in data_unres["items"])


def test_reconciliation_results_by_id():
    """Verifies GET /reconciliation/results filterable by exact reconciliation_id."""
    # Pick one result
    db = SessionLocal()
    try:
        sample = db.query(ReconciliationResult).first()
        sample_id = sample.reconciliation_id
    finally:
        db.close()

    resp = client.get(f"/reconciliation/results?reconciliation_id={sample_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["reconciliation_id"] == sample_id


# =============================================================================
# 3. Audit Trail API Tests
# =============================================================================

def test_get_audit_trail_listing():
    """Verifies GET /audit returns immutable audit log entries."""
    response = client.get("/audit?limit=25&offset=0")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] > 0
    assert len(data["items"]) <= 25

    entry = data["items"][0]
    assert "audit_id" in entry
    assert "timestamp" in entry
    assert "actor" in entry
    assert "action" in entry
    assert "entity" in entry
    assert "entity_id" in entry


def test_get_audit_trail_filters():
    """Verifies GET /audit filterable by entity, action, and entity_id."""
    # Filter by entity: RECONCILIATION
    resp_ent = client.get("/audit?entity=RECONCILIATION&limit=10")
    assert resp_ent.status_code == 200
    data_ent = resp_ent.json()
    assert data_ent["total"] > 0
    assert all(a["entity"] == "RECONCILIATION" for a in data_ent["items"])

    # Filter by action: RECONCILIATION_COMPLETED
    resp_act = client.get("/audit?action=RECONCILIATION_COMPLETED&limit=10")
    assert resp_act.status_code == 200
    data_act = resp_act.json()
    assert data_act["total"] > 0
    assert all(a["action"] == "RECONCILIATION_COMPLETED" for a in data_act["items"])


def test_audit_endpoint_strictly_read_only():
    """
    CRITICAL: Confirms that audit trail has no mutating HTTP methods (POST, PUT, DELETE).
    """
    # Attempt POST to /audit -> 405 Method Not Allowed
    resp_post = client.post("/audit", json={"actor": "HACKER", "action": "MUTATE"})
    assert resp_post.status_code == 405

    # Attempt PUT to /audit -> 405 Method Not Allowed
    resp_put = client.put("/audit", json={"actor": "HACKER", "action": "MUTATE"})
    assert resp_put.status_code == 405

    # Attempt DELETE to /audit -> 405 Method Not Allowed
    resp_del = client.delete("/audit")
    assert resp_del.status_code == 405


# =============================================================================
# 4. Reports / Operational Summary API Tests
# =============================================================================

def test_get_reports_summary():
    """Verifies GET /reports/summary returns live operational metrics from database."""
    response = client.get("/reports/summary")
    assert response.status_code == 200
    data = response.json()

    assert data["total_transactions"] >= 289
    assert data["total_reconciliation_results"] >= 100
    assert data["total_auto_reconciled"] >= 55
    assert data["total_exceptions"] > 0
    assert data["open_exceptions"] > 0
    assert data["auto_reconciliation_rate"] >= 50.0
    assert data["unresolved_amount_inr"] > 0.0
    assert isinstance(data["exceptions_by_severity"], dict)
    assert isinstance(data["exceptions_by_category"], dict)
    assert isinstance(data["sla_status_breakdown"], dict)


# =============================================================================
# 5. Validation and Boundary Error Tests
# =============================================================================

def test_pagination_bounds_validation():
    """Verifies validation errors when limit is out of allowed range."""
    # limit > 500
    resp_high = client.get("/transactions?limit=9999")
    assert resp_high.status_code == 422

    # limit < 1
    resp_low = client.get("/transactions?limit=0")
    assert resp_low.status_code == 422

    # offset < 0
    resp_neg = client.get("/transactions?offset=-1")
    assert resp_neg.status_code == 422

    # audit limit > 500
    resp_audit_high = client.get("/audit?limit=600")
    assert resp_audit_high.status_code == 422
