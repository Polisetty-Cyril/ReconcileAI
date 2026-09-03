"""
ReconcileAI - Finance Controller Step 1.5 Integration Tests
End-to-end integration test suite exercising the REAL FinanceController together with
the REAL DeterministicReconciliationEngine and the real SQLAlchemy SQLite database.

Verifies:
1. Exact-match integration: Real Transaction models (GW/BNK/ERP) -> AUTO_RECONCILED.
2. Discrepancy integration: Real Transaction models with amount mismatch -> HUMAN_REVIEW & OPEN exception.
3. DB-loaded integration: Real DB persistence of transactions -> loaded via controller.reconcile() without passing txns.
4. Persistence integration: Real DB persistence of results and exceptions via controller.reconcile(persist=True).
5. Stage-boundary integration: Confirms no FuzzyMatchEngine or AIController/Gemini invocations occur.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest
from sqlalchemy.orm import Session

from backend.database import SessionLocal, init_db
from backend.models.transaction import Transaction
from backend.models.reconciliation import ReconciliationResult
from backend.models.exception import ReconciliationException
from backend.services.finance_controller import FinanceController
from backend.services.reconciliation import DeterministicReconciliationEngine
from backend.services.fuzzy_matcher import FuzzyMatchEngine
from backend.services.ai_controller import AIController
from backend.services.llm_client import BaseLLMClient, GeminiLLMClient


# ---------------------------------------------------------------------------
# Database Isolation & Cleanup Helpers
# ---------------------------------------------------------------------------

def cleanup_fc_integration_records(db: Session) -> None:
    """
    Safely and reliably removes all records created by FC_INT integration test cases
    using actual transaction IDs and linked reconciliation IDs.
    """
    # 1. Delete exceptions directly referencing FC_INT transaction IDs
    db.query(ReconciliationException).filter(
        ReconciliationException.transaction_id.like("FC_INT_%")
    ).delete(synchronize_session=False)

    # 2. Find any ReconciliationResult records matching FC_INT transactions
    fc_results = db.query(ReconciliationResult).filter(
        (ReconciliationResult.gateway_transaction_id.like("FC_INT_%")) |
        (ReconciliationResult.bank_transaction_id.like("FC_INT_%")) |
        (ReconciliationResult.erp_invoice_id.like("FC_INT_%"))
    ).all()
    fc_recon_ids = [r.reconciliation_id for r in fc_results if r.reconciliation_id]

    # 3. Delete any exceptions linked to those reconciliation IDs
    if fc_recon_ids:
        db.query(ReconciliationException).filter(
            ReconciliationException.reconciliation_id.in_(fc_recon_ids)
        ).delete(synchronize_session=False)

    # 4. Delete the reconciliation results themselves
    if fc_recon_ids:
        db.query(ReconciliationResult).filter(
            ReconciliationResult.reconciliation_id.in_(fc_recon_ids)
        ).delete(synchronize_session=False)
    db.query(ReconciliationResult).filter(
        (ReconciliationResult.gateway_transaction_id.like("FC_INT_%")) |
        (ReconciliationResult.bank_transaction_id.like("FC_INT_%")) |
        (ReconciliationResult.erp_invoice_id.like("FC_INT_%"))
    ).delete(synchronize_session=False)

    # 5. Delete FC_INT transactions
    db.query(Transaction).filter(
        Transaction.transaction_id.like("FC_INT_%")
    ).delete(synchronize_session=False)

    db.commit()


@pytest.fixture(scope="module", autouse=True)
def setup_integration_test_db():
    """Initializes schema and cleans up integration test records before and after module."""
    init_db()
    db: Session = SessionLocal()
    try:
        cleanup_fc_integration_records(db)
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        cleanup_fc_integration_records(db)
    finally:
        db.close()


@pytest.fixture
def db_session():
    """Provides a transactional database session for tests with automatic cleanup."""
    db: Session = SessionLocal()
    try:
        cleanup_fc_integration_records(db)
        yield db
    finally:
        cleanup_fc_integration_records(db)
        db.close()


# ---------------------------------------------------------------------------
# 1. Exact-Match Integration Test
# ---------------------------------------------------------------------------

def test_exact_match_integration():
    """
    Construct realistic Transaction model objects representing a matching gateway/bank/ERP case.
    Run through the real FinanceController with real DeterministicReconciliationEngine.
    Verify the actual reconciliation outcome is AUTO_RECONCILED.
    """
    controller = FinanceController()
    base_date = datetime(2026, 8, 15, 10, 30, 0, tzinfo=timezone.utc)

    gw = Transaction(
        transaction_id="FC_INT_GW_EXACT_1",
        source="GATEWAY",
        reference_id="pay_fc_int_1001",
        order_id="ORD_INT_1001",
        customer_id="CUST_INT_1001",
        amount=4999.00,
        currency="INR",
        transaction_date=base_date,
        status="CAPTURED",
        transaction_type="PAYMENT",
        description="Payment captured for Order ORD_INT_1001",
    )
    bnk = Transaction(
        transaction_id="FC_INT_BNK_EXACT_1",
        source="BANK",
        reference_id="pay_fc_int_1001",
        order_id="ORD_INT_1001",
        customer_id="CUST_INT_1001",
        amount=4999.00,
        currency="INR",
        transaction_date=base_date + timedelta(days=1),
        status="CREDIT",
        transaction_type="SETTLEMENT",
        description="Settlement credit pay_fc_int_1001",
    )
    erp = Transaction(
        transaction_id="FC_INT_ERP_EXACT_1",
        source="ERP",
        reference_id="pay_fc_int_1001",
        order_id="ORD_INT_1001",
        customer_id="CUST_INT_1001",
        amount=4999.00,
        currency="INR",
        transaction_date=base_date,
        status="PAID",
        transaction_type="INVOICE",
        description="Invoice matched ORD_INT_1001",
    )

    summary = controller.reconcile(transactions=[gw, bnk, erp], persist=False)

    assert summary["total_clusters"] == 1
    assert summary["total_reconciled"] == 1
    assert summary["total_exceptions"] == 0
    assert summary["auto_reconciled_rate"] == 100.0

    result: ReconciliationResult = summary["results"][0]
    assert result.final_decision == "AUTO_RECONCILED"
    assert result.match_score == 100.0
    assert result.matching_method == "EXACT_RULE"
    assert result.is_resolved is True
    assert result.discrepancy_amount == 0.0
    assert result.gateway_transaction_id == "FC_INT_GW_EXACT_1"
    assert result.bank_transaction_id == "FC_INT_BNK_EXACT_1"
    assert result.erp_invoice_id == "FC_INT_ERP_EXACT_1"


# ---------------------------------------------------------------------------
# 2. Discrepancy Integration Test
# ---------------------------------------------------------------------------

def test_discrepancy_integration():
    """
    Construct realistic Transaction objects containing a genuine discrepancy (amount mismatch).
    Run through the real FinanceController with real DeterministicReconciliationEngine.
    Verify the actual result is HUMAN_REVIEW and an exception is produced.
    """
    controller = FinanceController()
    base_date = datetime(2026, 8, 15, 11, 0, 0, tzinfo=timezone.utc)

    gw = Transaction(
        transaction_id="FC_INT_GW_MISMATCH_1",
        source="GATEWAY",
        reference_id="pay_fc_int_1002",
        order_id="ORD_INT_1002",
        customer_id="CUST_INT_1002",
        amount=10000.00,
        currency="INR",
        transaction_date=base_date,
        status="CAPTURED",
        transaction_type="PAYMENT",
        description="Payment captured for Order ORD_INT_1002",
    )
    bnk = Transaction(
        transaction_id="FC_INT_BNK_MISMATCH_1",
        source="BANK",
        reference_id="pay_fc_int_1002",
        order_id="ORD_INT_1002",
        customer_id="CUST_INT_1002",
        amount=9500.00,
        currency="INR",
        transaction_date=base_date + timedelta(days=1),
        status="CREDIT",
        transaction_type="SETTLEMENT",
        description="Partial settlement credit pay_fc_int_1002",
    )

    summary = controller.reconcile(transactions=[gw, bnk], persist=False)

    assert summary["total_clusters"] == 1
    assert summary["total_reconciled"] == 0
    assert summary["total_review"] == 1
    assert summary["total_exceptions"] == 1

    result: ReconciliationResult = summary["results"][0]
    assert result.final_decision == "HUMAN_REVIEW"
    assert result.is_resolved is False
    assert result.discrepancy_amount == 500.00
    assert result.match_score == 50.0

    assert len(summary["exceptions"]) == 1
    exc: ReconciliationException = summary["exceptions"][0]
    assert exc.category == "AMOUNT_MISMATCH"
    assert exc.severity == "HIGH"
    assert exc.difference_amount == 500.00
    assert exc.status == "OPEN"
    assert exc.transaction_id == "FC_INT_GW_MISMATCH_1"
    assert exc.resolved_by is None
    assert exc.resolved_at is None


# ---------------------------------------------------------------------------
# 3. DB-Loaded Integration Test
# ---------------------------------------------------------------------------

def test_db_loaded_integration(db_session: Session):
    """
    Insert realistic test transactions into the test database.
    Instantiate FinanceController with the real test DB session.
    Call reconcile() without explicitly passing transactions.
    Verify transactions are loaded from the database and processed by the real reconciliation engine.
    """
    base_date = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)
    gw = Transaction(
        transaction_id="FC_INT_DBLOAD_GW_1",
        source="GATEWAY",
        reference_id="pay_fc_int_1003",
        order_id="ORD_INT_1003",
        customer_id="CUST_INT_1003",
        amount=7500.00,
        currency="INR",
        transaction_date=base_date,
        status="CAPTURED",
        transaction_type="PAYMENT",
        description="Database loaded gateway transaction",
    )
    bnk = Transaction(
        transaction_id="FC_INT_DBLOAD_BNK_1",
        source="BANK",
        reference_id="pay_fc_int_1003",
        order_id="ORD_INT_1003",
        customer_id="CUST_INT_1003",
        amount=7500.00,
        currency="INR",
        transaction_date=base_date + timedelta(days=1),
        status="CREDIT",
        transaction_type="SETTLEMENT",
        description="Database loaded bank settlement",
    )
    db_session.add_all([gw, bnk])
    db_session.commit()

    controller = FinanceController(db=db_session)
    # Call without passing transactions explicitly
    summary = controller.reconcile(persist=False)

    # Locate the reconciliation outcome corresponding to our test transaction
    matched_results = [
        r for r in summary["results"]
        if r.gateway_transaction_id == "FC_INT_DBLOAD_GW_1"
    ]
    assert len(matched_results) == 1
    res = matched_results[0]
    assert res.final_decision == "AUTO_RECONCILED"
    assert res.match_score == 100.0
    assert res.bank_transaction_id == "FC_INT_DBLOAD_BNK_1"
    assert res.is_resolved is True

    # Confirm that persist=False did not write results to the database table
    db_results = db_session.query(ReconciliationResult).filter(
        ReconciliationResult.gateway_transaction_id == "FC_INT_DBLOAD_GW_1"
    ).all()
    assert len(db_results) == 0


# ---------------------------------------------------------------------------
# 4. Persistence Integration Test
# ---------------------------------------------------------------------------

def test_persistence_integration(db_session: Session):
    """
    Insert realistic test transactions into the test database.
    Call FinanceController.reconcile(..., persist=True).
    Query the database afterward.
    Verify the expected ReconciliationResult and/or ReconciliationException records
    were actually persisted and contain the expected values.
    """
    base_date = datetime(2026, 8, 15, 14, 0, 0, tzinfo=timezone.utc)

    # Exact matching pair
    gw_exact = Transaction(
        transaction_id="FC_INT_PERSIST_GW_1",
        source="GATEWAY",
        reference_id="pay_fc_int_p1",
        order_id="ORD_INT_P1",
        amount=3200.00,
        currency="INR",
        transaction_date=base_date,
        status="CAPTURED",
        transaction_type="PAYMENT",
    )
    bnk_exact = Transaction(
        transaction_id="FC_INT_PERSIST_BNK_1",
        source="BANK",
        reference_id="pay_fc_int_p1",
        order_id="ORD_INT_P1",
        amount=3200.00,
        currency="INR",
        transaction_date=base_date + timedelta(days=1),
        status="CREDIT",
        transaction_type="SETTLEMENT",
    )

    # Discrepant pair (amount mismatch)
    gw_diff = Transaction(
        transaction_id="FC_INT_PERSIST_GW_2",
        source="GATEWAY",
        reference_id="pay_fc_int_p2",
        order_id="ORD_INT_P2",
        amount=6000.00,
        currency="INR",
        transaction_date=base_date,
        status="CAPTURED",
        transaction_type="PAYMENT",
    )
    bnk_diff = Transaction(
        transaction_id="FC_INT_PERSIST_BNK_2",
        source="BANK",
        reference_id="pay_fc_int_p2",
        order_id="ORD_INT_P2",
        amount=5500.00,
        currency="INR",
        transaction_date=base_date + timedelta(days=1),
        status="CREDIT",
        transaction_type="SETTLEMENT",
    )

    txns = [gw_exact, bnk_exact, gw_diff, bnk_diff]
    db_session.add_all(txns)
    db_session.commit()

    controller = FinanceController()
    summary = controller.reconcile(transactions=txns, db=db_session, persist=True)

    assert summary["total_clusters"] == 2
    assert summary["total_reconciled"] == 1
    assert summary["total_review"] == 1
    assert summary["total_exceptions"] == 1

    # Query database directly to confirm physical rows
    persisted_results = db_session.query(ReconciliationResult).filter(
        ReconciliationResult.gateway_transaction_id.in_(["FC_INT_PERSIST_GW_1", "FC_INT_PERSIST_GW_2"])
    ).all()
    assert len(persisted_results) == 2

    # Verify exact match row in DB
    exact_res = next(r for r in persisted_results if r.gateway_transaction_id == "FC_INT_PERSIST_GW_1")
    assert exact_res.final_decision == "AUTO_RECONCILED"
    assert exact_res.match_score == 100.0
    assert exact_res.is_resolved is True
    assert exact_res.discrepancy_amount == 0.0

    # Verify discrepancy row in DB
    diff_res = next(r for r in persisted_results if r.gateway_transaction_id == "FC_INT_PERSIST_GW_2")
    assert diff_res.final_decision == "HUMAN_REVIEW"
    assert diff_res.is_resolved is False
    assert diff_res.discrepancy_amount == 500.00

    # Verify exception row in DB
    persisted_exceptions = db_session.query(ReconciliationException).filter(
        ReconciliationException.transaction_id == "FC_INT_PERSIST_GW_2"
    ).all()
    assert len(persisted_exceptions) == 1
    exc = persisted_exceptions[0]
    assert exc.category == "AMOUNT_MISMATCH"
    assert exc.severity == "HIGH"
    assert exc.difference_amount == 500.00
    assert exc.status == "OPEN"
    assert exc.reconciliation_id == diff_res.reconciliation_id


# ---------------------------------------------------------------------------
# 5. Stage-Boundary Integration Test
# ---------------------------------------------------------------------------

def test_stage_boundary_integration():
    """
    Verify this integration test only exercises the current deterministic stage.
    Explicitly proves no FuzzyMatchEngine, AIController, or Gemini/LLM methods
    are invoked during end-to-end controller execution.
    """
    controller = FinanceController()
    base_date = datetime(2026, 8, 15, 15, 0, 0, tzinfo=timezone.utc)

    gw = Transaction(
        transaction_id="FC_INT_GW_BOUNDARY",
        source="GATEWAY",
        reference_id="pay_fc_int_b1",
        amount=8000.00,
        currency="INR",
        transaction_date=base_date,
        status="CAPTURED",
        transaction_type="PAYMENT",
    )
    bnk = Transaction(
        transaction_id="FC_INT_BNK_BOUNDARY",
        source="BANK",
        reference_id="pay_fc_int_b1",
        amount=7200.00,
        currency="INR",
        transaction_date=base_date,
        status="CREDIT",
        transaction_type="SETTLEMENT",
    )

    with patch.object(FuzzyMatchEngine, "score_pair") as mock_fuzzy_pair, \
         patch.object(FuzzyMatchEngine, "find_best_candidates") as mock_fuzzy_cand, \
         patch.object(AIController, "investigate") as mock_ai_inv, \
         patch.object(AIController, "investigate_with_fuzzy") as mock_ai_fuzz, \
         patch.object(AIController, "investigate_and_persist") as mock_ai_persist, \
         patch.object(BaseLLMClient, "reason") as mock_base_llm, \
         patch.object(GeminiLLMClient, "reason") as mock_gemini:

        summary = controller.reconcile(transactions=[gw, bnk], persist=False)

        # Confirm deterministic reconciliation ran
        assert summary["total_clusters"] == 1
        assert summary["total_review"] == 1

        # Confirm stage boundaries are completely respected
        assert mock_fuzzy_pair.call_count == 0
        assert mock_fuzzy_cand.call_count == 0
        assert mock_ai_inv.call_count == 0
        assert mock_ai_fuzz.call_count == 0
        assert mock_ai_persist.call_count == 0
        assert mock_base_llm.call_count == 0
        assert mock_gemini.call_count == 0
