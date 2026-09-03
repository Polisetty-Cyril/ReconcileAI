"""
ReconcileAI - Finance Controller Step 1 Test Suite
Tests the orchestration skeleton boundary around the deterministic reconciliation engine.

Verifies:
1. Basic in-memory reconciliation (exact match)
2. Discrepancy handling (unresolved result, open exception, no auto-resolution)
3. Database-loaded in-memory mode (loads transactions, no result persistence)
4. Persist mode (preserves engine persistence to DB)
5. Dependency injection & delegation (proves delegation to engine methods)
6. No fuzzy matching invocation (FuzzyMatchEngine is never called)
7. No AI invocation (AIController and Gemini/LLM are never called)
8. Safety boundaries (transaction amounts/statuses untouched, no exception approval)
9. Invalid usage error handling (persist=True or no txns without db raises ValueError)
10. run_stage1 alias behavior
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy.orm import Session

from backend.database import SessionLocal, init_db
from backend.models.transaction import Transaction
from backend.models.reconciliation import ReconciliationResult
from backend.models.exception import ReconciliationException
from backend.schemas.transaction import CanonicalTransaction
from backend.services.finance_controller import FinanceController
from backend.services.reconciliation import (
    DeterministicReconciliationEngine,
    ReconciliationReasonCode,
)
from backend.services.fuzzy_matcher import FuzzyMatchEngine
from backend.services.ai_controller import AIController
from backend.services.llm_client import BaseLLMClient, GeminiLLMClient


# ---------------------------------------------------------------------------
# Test Fixtures & Helpers
# ---------------------------------------------------------------------------

def cleanup_fc_test_records(db: Session) -> None:
    """
    Safely and reliably removes all records created by FC_TEST test cases
    using actual transaction IDs and linked reconciliation IDs.
    """
    # 1. Delete exceptions directly referencing FC_TEST transaction IDs
    db.query(ReconciliationException).filter(
        ReconciliationException.transaction_id.like("FC_TEST_%")
    ).delete(synchronize_session=False)

    # 2. Find any ReconciliationResult records matching FC_TEST transactions
    fc_results = db.query(ReconciliationResult).filter(
        (ReconciliationResult.gateway_transaction_id.like("FC_TEST_%")) |
        (ReconciliationResult.bank_transaction_id.like("FC_TEST_%")) |
        (ReconciliationResult.erp_invoice_id.like("FC_TEST_%"))
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
        (ReconciliationResult.gateway_transaction_id.like("FC_TEST_%")) |
        (ReconciliationResult.bank_transaction_id.like("FC_TEST_%")) |
        (ReconciliationResult.erp_invoice_id.like("FC_TEST_%"))
    ).delete(synchronize_session=False)

    # 5. Delete FC_TEST transactions
    db.query(Transaction).filter(
        Transaction.transaction_id.like("FC_TEST_%")
    ).delete(synchronize_session=False)

    db.commit()


@pytest.fixture(scope="module", autouse=True)
def setup_test_db():
    """Initializes schema and cleans up test records before and after test module."""
    init_db()
    db: Session = SessionLocal()
    try:
        cleanup_fc_test_records(db)
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        cleanup_fc_test_records(db)
    finally:
        db.close()


@pytest.fixture
def db_session():
    """Provides a transactional database session for tests with automatic cleanup."""
    db: Session = SessionLocal()
    try:
        cleanup_fc_test_records(db)
        yield db
    finally:
        cleanup_fc_test_records(db)
        db.close()


def make_canonical_txn(
    txn_id: str,
    source: str,
    ref_id: str = "pay_fc_100",
    order_id: str = "ORD_FC_100",
    amount: float = 2500.0,
    txn_date: datetime = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc),
    status: str = "CAPTURED",
    txn_type: str = "PAYMENT",
) -> CanonicalTransaction:
    """Helper to construct CanonicalTransaction fixtures."""
    return CanonicalTransaction(
        transaction_id=txn_id,
        source=source,
        reference_id=ref_id,
        order_id=order_id,
        customer_id="CUST_FC_100",
        amount=amount,
        currency="INR",
        transaction_date=txn_date,
        status=status,
        transaction_type=txn_type,
        description=f"{source} transaction {txn_id}",
        metadata={},
    )


# ---------------------------------------------------------------------------
# 1. Basic In-Memory Reconciliation (Exact Match)
# ---------------------------------------------------------------------------

def test_basic_in_memory_reconciliation_exact_match():
    """
    Verify FinanceController runs deterministic reconciliation in memory
    for an exact match scenario without requiring a database session.
    """
    controller = FinanceController()
    base_date = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)

    gw = make_canonical_txn("FC_TEST_GW_1", "GATEWAY", ref_id="pay_fc_1", amount=2500.0, txn_date=base_date, status="CAPTURED")
    bnk = make_canonical_txn("FC_TEST_BNK_1", "BANK", ref_id="pay_fc_1", amount=2500.0, txn_date=base_date + timedelta(days=1), status="CREDIT", txn_type="SETTLEMENT")
    erp = make_canonical_txn("FC_TEST_ERP_1", "ERP", ref_id="pay_fc_1", amount=2500.0, txn_date=base_date, status="PAID", txn_type="INVOICE")

    summary = controller.reconcile(transactions=[gw, bnk, erp], persist=False)

    # Verify summary structure
    assert isinstance(summary, dict)
    for key in ("total_clusters", "total_reconciled", "total_review", "total_exceptions", "auto_reconciled_rate", "results", "exceptions"):
        assert key in summary

    assert summary["total_clusters"] == 1
    assert summary["total_reconciled"] == 1
    assert summary["total_exceptions"] == 0
    assert summary["auto_reconciled_rate"] == 100.0

    # Verify result model
    result: ReconciliationResult = summary["results"][0]
    assert result.final_decision == "AUTO_RECONCILED"
    assert result.match_score == 100.0
    assert result.matching_method == "EXACT_RULE"
    assert result.discrepancy_amount == 0.0
    assert result.is_resolved is True


# ---------------------------------------------------------------------------
# 2. Discrepancy Scenario Handling
# ---------------------------------------------------------------------------

def test_discrepancy_scenario_remains_unresolved():
    """
    Verify FinanceController produces an unresolved ReconciliationResult and
    ReconciliationException with status='OPEN' for an amount mismatch scenario.
    Controller must not resolve, approve, or reject anything.
    """
    controller = FinanceController()
    base_date = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)

    gw = make_canonical_txn("FC_TEST_GW_2", "GATEWAY", ref_id="pay_fc_2", amount=5000.0, txn_date=base_date, status="CAPTURED")
    bnk = make_canonical_txn("FC_TEST_BNK_2", "BANK", ref_id="pay_fc_2", amount=4500.0, txn_date=base_date + timedelta(days=1), status="CREDIT", txn_type="SETTLEMENT")

    summary = controller.reconcile(transactions=[gw, bnk], persist=False)

    assert summary["total_clusters"] == 1
    assert summary["total_reconciled"] == 0
    assert summary["total_review"] == 1
    assert summary["total_exceptions"] == 1

    # ReconciliationResult must be unresolved
    result: ReconciliationResult = summary["results"][0]
    assert result.final_decision == "HUMAN_REVIEW"
    assert result.is_resolved is False
    assert result.discrepancy_amount == 500.0

    # ReconciliationException must be OPEN and untouched
    assert len(summary["exceptions"]) == 1
    exc: ReconciliationException = summary["exceptions"][0]
    assert exc.status == "OPEN"
    assert exc.category == "AMOUNT_MISMATCH"
    assert exc.difference_amount == 500.0
    assert exc.resolved_by is None
    assert exc.resolved_at is None
    assert exc.reviewer_notes is None


# ---------------------------------------------------------------------------
# 3. Database-Loaded In-Memory Mode
# ---------------------------------------------------------------------------

def test_database_loaded_in_memory_mode():
    """
    Verify FinanceController loads transactions from database session when
    transactions argument is None, runs in-memory reconciliation, and does
    NOT write reconciliation results or exceptions to the database when persist=False.
    Uses an isolated session query mock to test database retrieval cleanly.
    """
    base_date = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)
    t1 = Transaction(
        transaction_id="FC_TEST_DB_GW_1",
        source="GATEWAY",
        reference_id="pay_fc_db_1",
        order_id="ORD_FC_DB_1",
        amount=3000.0,
        currency="INR",
        transaction_date=base_date,
        status="CAPTURED",
        transaction_type="PAYMENT",
    )
    t2 = Transaction(
        transaction_id="FC_TEST_DB_BNK_1",
        source="BANK",
        reference_id="pay_fc_db_1",
        order_id="ORD_FC_DB_1",
        amount=3000.0,
        currency="INR",
        transaction_date=base_date + timedelta(days=1),
        status="CREDIT",
        transaction_type="SETTLEMENT",
    )

    # Isolate Transaction query so query(Transaction).all() returns exactly the two test transactions
    mock_db = MagicMock(spec=Session)
    mock_query = MagicMock()
    mock_db.query.return_value = mock_query
    mock_query.all.return_value = [t1, t2]

    controller = FinanceController(db=mock_db)
    summary = controller.reconcile(persist=False)

    # 1. Verify FinanceController queried the database for Transaction models
    mock_db.query.assert_called_once_with(Transaction)
    mock_query.all.assert_called_once()

    # 2. Verify deterministic reconciliation produced the expected outcome
    assert summary["total_clusters"] == 1
    assert summary["total_reconciled"] == 1
    assert summary["total_exceptions"] == 0
    assert summary["results"][0].final_decision == "AUTO_RECONCILED"
    assert summary["results"][0].match_score == 100.0

    # 3. Verify no persistence methods (add, commit) were called on the session
    mock_db.add.assert_not_called()
    mock_db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Database Persist Mode
# ---------------------------------------------------------------------------

def test_persist_mode_preserves_pipeline_persistence(db_session: Session):
    """
    Verify FinanceController persists ReconciliationResult and ReconciliationException
    to the database when persist=True.
    """
    cleanup_fc_test_records(db_session)
    base_date = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)
    gw_exact = Transaction(
        transaction_id="FC_TEST_P_GW_1",
        source="GATEWAY",
        reference_id="pay_fc_p_1",
        order_id="ORD_FC_P_1",
        amount=1500.0,
        currency="INR",
        transaction_date=base_date,
        status="CAPTURED",
        transaction_type="PAYMENT",
    )
    bnk_exact = Transaction(
        transaction_id="FC_TEST_P_BNK_1",
        source="BANK",
        reference_id="pay_fc_p_1",
        order_id="ORD_FC_P_1",
        amount=1500.0,
        currency="INR",
        transaction_date=base_date + timedelta(days=1),
        status="CREDIT",
        transaction_type="SETTLEMENT",
    )
    gw_mismatch = Transaction(
        transaction_id="FC_TEST_P_GW_2",
        source="GATEWAY",
        reference_id="pay_fc_p_2",
        order_id="ORD_FC_P_2",
        amount=2000.0,
        currency="INR",
        transaction_date=base_date,
        status="CAPTURED",
        transaction_type="PAYMENT",
    )
    bnk_mismatch = Transaction(
        transaction_id="FC_TEST_P_BNK_2",
        source="BANK",
        reference_id="pay_fc_p_2",
        order_id="ORD_FC_P_2",
        amount=1800.0,
        currency="INR",
        transaction_date=base_date + timedelta(days=1),
        status="CREDIT",
        transaction_type="SETTLEMENT",
    )
    txns = [gw_exact, bnk_exact, gw_mismatch, bnk_mismatch]
    db_session.add_all(txns)
    db_session.commit()

    controller = FinanceController()
    summary = controller.reconcile(transactions=txns, db=db_session, persist=True)

    assert summary["total_clusters"] == 2
    assert summary["total_reconciled"] == 1
    assert summary["total_review"] == 1
    assert summary["total_exceptions"] == 1

    # Verify rows were persisted to database
    saved_results = db_session.query(ReconciliationResult).filter(
        ReconciliationResult.gateway_transaction_id.in_(["FC_TEST_P_GW_1", "FC_TEST_P_GW_2"])
    ).all()
    assert len(saved_results) == 2

    saved_exceptions = db_session.query(ReconciliationException).filter(
        ReconciliationException.transaction_id == "FC_TEST_P_GW_2"
    ).all()
    assert len(saved_exceptions) == 1
    assert saved_exceptions[0].category == "AMOUNT_MISMATCH"
    assert saved_exceptions[0].difference_amount == 200.0
    assert saved_exceptions[0].status == "OPEN"


# ---------------------------------------------------------------------------
# 5. Dependency Injection / Delegation
# ---------------------------------------------------------------------------

def test_delegation_to_reconciliation_engine():
    """
    Verify FinanceController delegates directly to the deterministic reconciliation
    engine rather than implementing custom reconciliation loops:
    - Calls reconcile_transactions() when persist=False
    - Calls run_reconciliation_pipeline() when persist=True
    """
    mock_engine = MagicMock(spec=DeterministicReconciliationEngine)
    mock_engine.reconcile_transactions.return_value = {
        "total_clusters": 1,
        "total_reconciled": 1,
        "total_review": 0,
        "total_exceptions": 0,
        "auto_reconciled_rate": 100.0,
        "results": [],
        "exceptions": [],
    }
    mock_engine.run_reconciliation_pipeline.return_value = {
        "total_clusters": 1,
        "total_reconciled": 1,
        "total_review": 0,
        "total_exceptions": 0,
        "auto_reconciled_rate": 100.0,
        "results": [],
        "exceptions": [],
    }

    controller = FinanceController(engine=mock_engine)
    dummy_txns = [make_canonical_txn("FC_DUMMY_1", "GATEWAY")]

    # 1. Test persist=False delegation
    res_in_memory = controller.reconcile(transactions=dummy_txns, persist=False)
    assert res_in_memory["total_clusters"] == 1
    mock_engine.reconcile_transactions.assert_called_once_with(dummy_txns)
    mock_engine.run_reconciliation_pipeline.assert_not_called()

    # 2. Test persist=True delegation
    mock_db = MagicMock(spec=Session)
    res_persisted = controller.reconcile(transactions=dummy_txns, db=mock_db, persist=True)
    assert res_persisted["total_clusters"] == 1
    mock_engine.run_reconciliation_pipeline.assert_called_once_with(mock_db, transactions=dummy_txns)


# ---------------------------------------------------------------------------
# 6. No Fuzzy Matching Invocation
# ---------------------------------------------------------------------------

def test_no_fuzzy_matching_invoked():
    """
    Verify that Stage 1 FinanceController does NOT invoke FuzzyMatchEngine,
    even when reconciliation encounters discrepancies.
    """
    controller = FinanceController()
    base_date = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)

    gw = make_canonical_txn("FC_TEST_GW_NO_FUZZ", "GATEWAY", ref_id="pay_1045", amount=1000.0, txn_date=base_date)
    bnk = make_canonical_txn("FC_TEST_BNK_NO_FUZZ", "BANK", ref_id="PAY-1045", amount=1000.0, txn_date=base_date)

    with patch.object(FuzzyMatchEngine, "score_pair") as mock_score_pair, \
         patch.object(FuzzyMatchEngine, "find_best_candidates") as mock_candidates:
        controller.reconcile(transactions=[gw, bnk], persist=False)
        assert mock_score_pair.call_count == 0
        assert mock_candidates.call_count == 0


# ---------------------------------------------------------------------------
# 7. No AI / Gemini Invocation
# ---------------------------------------------------------------------------

def test_no_ai_or_gemini_invoked():
    """
    Verify that Stage 1 FinanceController does NOT instantiate or invoke
    AIController, Gemini, or any LLM reasoning methods.
    """
    controller = FinanceController()
    base_date = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)

    # Anomaly/discrepancy scenario
    gw = make_canonical_txn("FC_TEST_GW_NO_AI", "GATEWAY", ref_id="pay_no_ai", amount=5000.0, txn_date=base_date)
    bnk = make_canonical_txn("FC_TEST_BNK_NO_AI", "BANK", ref_id="pay_no_ai", amount=4000.0, txn_date=base_date)

    with patch.object(AIController, "investigate") as mock_investigate, \
         patch.object(AIController, "investigate_with_fuzzy") as mock_inv_fuzzy, \
         patch.object(AIController, "investigate_and_persist") as mock_inv_persist, \
         patch.object(BaseLLMClient, "reason") as mock_base_reason:
        controller.reconcile(transactions=[gw, bnk], persist=False)
        assert mock_investigate.call_count == 0
        assert mock_inv_fuzzy.call_count == 0
        assert mock_inv_persist.call_count == 0
        assert mock_base_reason.call_count == 0


# ---------------------------------------------------------------------------
# 8. Financial Safety Invariants
# ---------------------------------------------------------------------------

def test_financial_safety_invariants():
    """
    Verify that FinanceController preserves all financial safety invariants:
    - Does NOT alter transaction amounts or statuses
    - Does NOT set exception status to APPROVED, REJECTED, or RESOLVED
    - Does NOT mark ReconciliationResult.is_resolved=True for discrepancies
    - Does NOT alter deterministic final_decision beyond engine rules
    """
    controller = FinanceController()
    base_date = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)

    gw = make_canonical_txn("FC_TEST_GW_SAFE", "GATEWAY", ref_id="pay_safe", amount=9999.99, txn_date=base_date, status="CAPTURED")
    bnk = make_canonical_txn("FC_TEST_BNK_SAFE", "BANK", ref_id="pay_safe", amount=8888.88, txn_date=base_date, status="CREDIT")

    summary = controller.reconcile(transactions=[gw, bnk], persist=False)

    # 1. Transactions remained completely untouched
    assert gw.amount == 9999.99
    assert gw.status == "CAPTURED"
    assert bnk.amount == 8888.88
    assert bnk.status == "CREDIT"

    # 2. Result is strictly unresolved
    res = summary["results"][0]
    assert res.is_resolved is False
    assert res.final_decision == "HUMAN_REVIEW"
    assert res.discrepancy_amount == round(abs(9999.99 - 8888.88), 2)

    # 3. Exception status remains strictly OPEN
    exc = summary["exceptions"][0]
    assert exc.status == "OPEN"
    assert exc.status not in ("APPROVED", "REJECTED", "RESOLVED")
    assert exc.resolved_by is None
    assert exc.resolved_at is None


# ---------------------------------------------------------------------------
# 9. Invalid Usage Handling
# ---------------------------------------------------------------------------

def test_invalid_persist_usage_raises_error():
    """
    Verify that calling reconcile() with persist=True without providing
    a database session raises a ValueError.
    """
    controller = FinanceController()
    with pytest.raises(ValueError, match="A database session \\(db\\) is required when persist=True."):
        controller.reconcile(persist=True)


def test_missing_transactions_and_db_raises_error():
    """
    Verify that calling reconcile() without transactions and without a
    database session raises a ValueError.
    """
    controller = FinanceController()
    with pytest.raises(ValueError, match="Must provide either 'transactions' list or an active database session 'db'."):
        controller.reconcile()


# ---------------------------------------------------------------------------
# 10. run_stage1 Alias
# ---------------------------------------------------------------------------

def test_run_stage1_alias_delegates_to_reconcile():
    """
    Verify that run_stage1() is an alias that behaves identically to reconcile().
    """
    controller = FinanceController()
    base_date = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)

    gw = make_canonical_txn("FC_TEST_GW_S1", "GATEWAY", ref_id="pay_s1", amount=1200.0, txn_date=base_date)
    bnk = make_canonical_txn("FC_TEST_BNK_S1", "BANK", ref_id="pay_s1", amount=1200.0, txn_date=base_date + timedelta(days=1))

    summary = controller.run_stage1(transactions=[gw, bnk], persist=False)

    assert summary["total_clusters"] == 1
    assert summary["total_reconciled"] == 1
    assert summary["total_exceptions"] == 0
    assert summary["results"][0].final_decision == "AUTO_RECONCILED"
