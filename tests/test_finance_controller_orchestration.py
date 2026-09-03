# Service-level integration tests for FinanceController Step 3C.
"""
ReconcileAI - Finance Controller Step 3C Orchestration Integration Tests

Verifies the unified orchestration workflow:
Observe -> Deterministic Reconcile -> Detect Anomaly -> Fuzzy Investigate -> AI Reason -> Return Enriched Summary

Safety Invariants Tested:
- AI cannot approve or reject an exception.
- AI cannot set result.is_resolved = True for discrepancies.
- exception.status strictly remains OPEN.
- Transaction amounts and balances remain unchanged.
- AI recommendations remain strictly advisory.
- Existing deterministic summary keys are preserved.
- No real Gemini or network calls are made.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy.orm import Session

from backend.database import SessionLocal, init_db
from backend.models.audit import AuditLog
from backend.models.transaction import Transaction
from backend.models.reconciliation import ReconciliationResult
from backend.models.exception import ReconciliationException
from backend.schemas.ai_controller import AIControllerResult
from backend.services.finance_controller import FinanceController
from backend.services.fuzzy_matcher import FuzzyMatchEngine, FuzzyMatchResult
from backend.services.ai_controller import AIController
from backend.services.llm_client import BaseLLMClient


# ---------------------------------------------------------------------------
# Test Isolation Fixtures & Recording Mocks
# ---------------------------------------------------------------------------

def cleanup_fc_orch_records(db: Session) -> None:
    """Safely removes records created with FC_ORCH_ identifiers."""
    db.query(AuditLog).filter(
        (AuditLog.audit_id.like("AUD_AI_%")) | (AuditLog.entity_id.like("REC_FC_ORCH_%"))
    ).delete(synchronize_session=False)

    db.query(ReconciliationException).filter(
        (ReconciliationException.exception_id.like("EXC_FC_ORCH_%")) |
        (ReconciliationException.reconciliation_id.like("REC_FC_ORCH_%")) |
        (ReconciliationException.transaction_id.like("FC_ORCH_%"))
    ).delete(synchronize_session=False)

    db.query(ReconciliationResult).filter(
        (ReconciliationResult.reconciliation_id.like("REC_FC_ORCH_%")) |
        (ReconciliationResult.gateway_transaction_id.like("FC_ORCH_%")) |
        (ReconciliationResult.bank_transaction_id.like("FC_ORCH_%"))
    ).delete(synchronize_session=False)

    db.query(Transaction).filter(
        Transaction.transaction_id.like("FC_ORCH_%")
    ).delete(synchronize_session=False)

    db.commit()


@pytest.fixture(scope="module", autouse=True)
def setup_orch_test_db():
    """Initializes schema and cleans up test records before and after test module."""
    init_db()
    db: Session = SessionLocal()
    try:
        cleanup_fc_orch_records(db)
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        cleanup_fc_orch_records(db)
    finally:
        db.close()


@pytest.fixture
def db_session():
    """Provides a transactional database session for tests with automatic cleanup."""
    db: Session = SessionLocal()
    try:
        cleanup_fc_orch_records(db)
        yield db
    finally:
        cleanup_fc_orch_records(db)
        db.close()


class RecordingMockLLMClient(BaseLLMClient):
    """Deterministic mock client that records received evidence and returns structured responses."""

    def __init__(self, return_dict: Optional[Dict[str, Any]] = None) -> None:
        self.recorded_evidence: List[Dict[str, Any]] = []
        self.call_count: int = 0
        self.return_dict = return_dict or {
            "recommendation": "REVIEW",
            "confidence": 0.85,
            "reason": "Default advisory review recommendation.",
            "risk": "MEDIUM",
        }

    def reason(self, evidence: Dict[str, Any]) -> Dict[str, Any]:
        self.call_count += 1
        self.recorded_evidence.append(evidence)
        return self.return_dict


def make_orch_txn(
    txn_id: str,
    source: str,
    ref_id: str,
    amount: float,
    order_id: str = "ORD_FC_ORCH_1",
    desc: str = "Payment transaction",
    customer_name: str = "Pooja Sharma",
    days_offset: int = 0,
    status: str = "CAPTURED",
    txn_type: str = "PAYMENT",
) -> Transaction:
    """Helper to build realistic Transaction models."""
    base_date = datetime(2026, 8, 28, 10, 0, 0, tzinfo=timezone.utc)
    txn = Transaction(
        transaction_id=txn_id,
        source=source,
        reference_id=ref_id,
        order_id=order_id,
        customer_id="CUST_FC_ORCH_1",
        amount=amount,
        currency="INR",
        transaction_date=base_date + timedelta(days=days_offset),
        status=status,
        transaction_type=txn_type,
        description=desc,
    )
    txn.customer_name = customer_name
    return txn


# ---------------------------------------------------------------------------
# Test 1: Reconcile and Investigate Mixed Batch
# ---------------------------------------------------------------------------

def test_reconcile_and_investigate_mixed_batch():
    """
    Create a realistic mixed batch:
    - Cluster 1: Exact match (GW + Bank)
    - Cluster 2: Discrepancy (GW + Bank amount mismatch)

    Verify:
    - Deterministic reconciliation executes.
    - Both results are produced.
    - AI results are returned for both.
    - Exact match receives AUTO_RECONCILE.
    - Discrepancy receives an advisory recommendation.
    """
    mock_client = RecordingMockLLMClient({
        "recommendation": "ESCALATE",
        "confidence": 0.94,
        "reason": "Amount discrepancy requires escalation.",
        "risk": "HIGH",
    })
    ai_controller = AIController(client=mock_client)
    controller = FinanceController(ai_controller=ai_controller)

    # Exact match cluster
    gw_exact = make_orch_txn("FC_ORCH_GW_1", "GATEWAY", ref_id="pay_orch_exact", amount=2000.0, order_id="ORD_EXACT")
    bnk_exact = make_orch_txn("FC_ORCH_BNK_1", "BANK", ref_id="pay_orch_exact", amount=2000.0, order_id="ORD_EXACT", days_offset=1, status="CREDIT", txn_type="SETTLEMENT")

    # Discrepancy cluster (amount mismatch)
    gw_disc = make_orch_txn("FC_ORCH_GW_2", "GATEWAY", ref_id="pay_orch_disc", amount=5000.0, order_id="ORD_DISC")
    bnk_disc = make_orch_txn("FC_ORCH_BNK_2", "BANK", ref_id="pay_orch_disc", amount=4500.0, order_id="ORD_DISC", days_offset=1, status="CREDIT", txn_type="SETTLEMENT")

    summary = controller.reconcile_and_investigate(
        transactions=[gw_exact, bnk_exact, gw_disc, bnk_disc],
        persist=False,
    )

    # 1. Verify deterministic counts
    assert summary["total_clusters"] == 2
    assert summary["total_reconciled"] == 1
    assert summary["total_review"] == 1
    assert summary["total_exceptions"] == 1

    # 2. Verify AI results returned
    ai_results = summary["ai_results"]
    assert len(ai_results) == 2

    # Verify recommendations: one AUTO_RECONCILE, one ESCALATE
    recs = [ar.recommendation for ar in ai_results]
    assert "AUTO_RECONCILE" in recs
    assert "ESCALATE" in recs

    # Confirm LLM was called only once (for the discrepancy; exact match was fast-pathed)
    assert mock_client.call_count == 1


# ---------------------------------------------------------------------------
# Test 2: Orchestration Invokes Fuzzy Before AI for Discrepancy
# ---------------------------------------------------------------------------

def test_orchestration_invokes_fuzzy_before_ai_for_discrepancy():
    """
    Use real FinanceController + real FuzzyMatchEngine + injected AIController with mock client.
    Verify the discrepancy flows:
        reconciliation -> fuzzy investigation -> AI reasoning
    and the fuzzy evidence reaches the AI client.
    """
    mock_client = RecordingMockLLMClient({
        "recommendation": "REVIEW",
        "confidence": 0.88,
        "reason": "Fuzzy reference similarity suggests typographical variation.",
        "risk": "MEDIUM",
    })
    ai_controller = AIController(client=mock_client)
    controller = FinanceController(ai_controller=ai_controller)

    # Similar references: "pay_fz_1045" vs "PAY-FZ-1045"
    gw = make_orch_txn("FC_ORCH_GW_FZ", "GATEWAY", ref_id="pay_fz_1045", amount=3000.0, desc="Razorpay payment pay_fz_1045")
    bnk = make_orch_txn("FC_ORCH_BNK_FZ", "BANK", ref_id="PAY-FZ-1045", amount=2800.0, desc="NEFT settlement PAY-FZ-1045", days_offset=1)

    summary = controller.reconcile_and_investigate(transactions=[gw, bnk], persist=False)

    assert summary["total_clusters"] == 1
    assert summary["total_review"] == 1

    # Verify fuzzy evidence reached the AI client
    assert mock_client.call_count == 1
    evidence = mock_client.recorded_evidence[0]
    assert "fuzzy_decision" in evidence
    assert evidence["fuzzy_decision"] == "FUZZY_MATCHED"
    assert evidence["fuzzy_composite_score"] >= 85.0


# ---------------------------------------------------------------------------
# Test 3: Orchestration Auto Match Skips Fuzzy and LLM
# ---------------------------------------------------------------------------

def test_orchestration_auto_match_skips_fuzzy_and_llm():
    """
    For an exact deterministic match, verify:
    - FuzzyMatchEngine is NOT invoked.
    - LLM client is NOT invoked.
    - AI fast-path AUTO_RECONCILE result is returned.
    """
    mock_client = RecordingMockLLMClient()
    mock_fuzzy = MagicMock(spec=FuzzyMatchEngine)
    ai_controller = AIController(client=mock_client, fuzzy_engine=mock_fuzzy)
    controller = FinanceController(fuzzy_engine=mock_fuzzy, ai_controller=ai_controller)

    gw = make_orch_txn("FC_ORCH_GW_EX", "GATEWAY", ref_id="pay_exact_skip", amount=1500.0)
    bnk = make_orch_txn("FC_ORCH_BNK_EX", "BANK", ref_id="pay_exact_skip", amount=1500.0, days_offset=1)

    summary = controller.reconcile_and_investigate(transactions=[gw, bnk], persist=False)

    assert summary["total_reconciled"] == 1
    assert len(summary["ai_results"]) == 1
    assert summary["ai_results"][0].recommendation == "AUTO_RECONCILE"
    assert summary["ai_results"][0].confidence == 1.0

    # Strict isolation: 0 fuzzy calls, 0 LLM calls
    mock_fuzzy.score_pair.assert_not_called()
    mock_fuzzy.find_best_candidates.assert_not_called()
    assert mock_client.call_count == 0


# ---------------------------------------------------------------------------
# Test 4: Orchestration Correlates Exception by Identifier (Non-Positional)
# ---------------------------------------------------------------------------

def test_orchestration_correlates_exception_by_identifier():
    """
    Create a batch with multiple discrepancies where results and exceptions
    are correlated strictly by reconciliation_id, not by index.
    """
    mock_client = RecordingMockLLMClient()
    ai_controller = AIController(client=mock_client)
    controller = FinanceController(ai_controller=ai_controller)

    # Two distinct discrepancies
    gw_a = make_orch_txn("FC_ORCH_GW_A", "GATEWAY", ref_id="pay_orch_a", amount=1000.0, order_id="ORD_A")
    bnk_a = make_orch_txn("FC_ORCH_BNK_A", "BANK", ref_id="pay_orch_a", amount=800.0, order_id="ORD_A")

    gw_b = make_orch_txn("FC_ORCH_GW_B", "GATEWAY", ref_id="pay_orch_b", amount=5000.0, order_id="ORD_B")
    bnk_b = make_orch_txn("FC_ORCH_BNK_B", "BANK", ref_id="pay_orch_b", amount=4000.0, order_id="ORD_B")

    summary = controller.reconcile_and_investigate(transactions=[gw_a, bnk_a, gw_b, bnk_b], persist=False)

    assert summary["total_review"] == 2
    assert len(summary["exceptions"]) == 2
    assert mock_client.call_count == 2

    # Verify that each evidence payload has matching reconciliation_id and difference_amount
    recon_a = next(r.reconciliation_id for r in summary["results"] if r.gateway_transaction_id == "FC_ORCH_GW_A")
    recon_b = next(r.reconciliation_id for r in summary["results"] if r.gateway_transaction_id == "FC_ORCH_GW_B")

    ev_a = next(e for e in mock_client.recorded_evidence if e["reconciliation_id"] == recon_a)
    ev_b = next(e for e in mock_client.recorded_evidence if e["reconciliation_id"] == recon_b)

    assert ev_a["difference_amount"] == 200.0
    assert ev_b["difference_amount"] == 1000.0


# ---------------------------------------------------------------------------
# Test 5: Safety Boundary - AI Cannot Override Unresolved Decisions
# ---------------------------------------------------------------------------

def test_orchestration_preserves_advisory_boundary():
    """
    Force the AI client to return recommendation="AUTO_RECONCILE" for a discrepancy.
    Verify:
    - result.is_resolved remains False
    - exception.status remains OPEN
    - financial amounts remain unchanged
    - final_decision remains HUMAN_REVIEW
    """
    mock_client = RecordingMockLLMClient({
        "recommendation": "AUTO_RECONCILE",
        "confidence": 0.99,
        "reason": "AI erroneously suggests auto reconciliation.",
        "risk": "LOW",
    })
    ai_controller = AIController(client=mock_client)
    controller = FinanceController(ai_controller=ai_controller)

    gw = make_orch_txn("FC_ORCH_GW_SAFE", "GATEWAY", ref_id="pay_safe_test", amount=7777.0)
    bnk = make_orch_txn("FC_ORCH_BNK_SAFE", "BANK", ref_id="pay_safe_test", amount=7000.0)

    summary = controller.reconcile_and_investigate(transactions=[gw, bnk], persist=False)

    # 1. Deterministic result is strictly preserved
    result: ReconciliationResult = summary["results"][0]
    assert result.final_decision == "HUMAN_REVIEW"
    assert result.is_resolved is False
    assert result.discrepancy_amount == 777.0

    # 2. Exception remains strictly OPEN
    exc: ReconciliationException = summary["exceptions"][0]
    assert exc.status == "OPEN"
    assert exc.status not in ("APPROVED", "REJECTED", "RESOLVED")

    # 3. Transaction amounts untouched
    assert gw.amount == 7777.0
    assert bnk.amount == 7000.0

    # 4. AI recommendation is present as advisory
    ai_res = summary["ai_results"][0]
    assert ai_res.recommendation == "AUTO_RECONCILE"


# ---------------------------------------------------------------------------
# Test 6: Persist Mode Populates AI Fields and Audit Records
# ---------------------------------------------------------------------------

def test_orchestration_persist_mode(db_session: Session):
    """
    Run reconcile_and_investigate(persist=True).
    Verify:
    - Results and exceptions are staged/persisted.
    - AI columns (ai_recommendation, ai_confidence, ai_reasoning) are populated.
    - AuditLog entries with action='AI_REASONED' are created.
    - FinanceController does not commit from investigate_with_ai.
    """
    mock_client = RecordingMockLLMClient({
        "recommendation": "REVIEW",
        "confidence": 0.85,
        "reason": "Settlement difference requires manual review.",
        "risk": "MEDIUM",
    })
    ai_controller = AIController(client=mock_client)
    controller = FinanceController(db=db_session, ai_controller=ai_controller)

    gw = make_orch_txn("FC_ORCH_GW_PERSIST", "GATEWAY", ref_id="pay_persist_orch", amount=4000.0)
    bnk = make_orch_txn("FC_ORCH_BNK_PERSIST", "BANK", ref_id="pay_persist_orch", amount=3600.0)

    db_session.add_all([gw, bnk])
    db_session.commit()

    summary = controller.reconcile_and_investigate(
        transactions=[gw, bnk],
        db=db_session,
        persist=True,
    )

    db_session.commit()

    # Verify persisted result has AI columns populated
    db_result = db_session.query(ReconciliationResult).filter(
        ReconciliationResult.gateway_transaction_id == "FC_ORCH_GW_PERSIST"
    ).first()
    assert db_result is not None
    assert db_result.ai_recommendation == "REVIEW"
    assert db_result.ai_confidence == 85.0
    assert "Settlement difference" in db_result.ai_reasoning
    assert db_result.is_resolved is False

    # Verify persisted exception has ai_explanation populated
    db_exc = db_session.query(ReconciliationException).filter(
        ReconciliationException.transaction_id == "FC_ORCH_GW_PERSIST"
    ).first()
    assert db_exc is not None
    assert db_exc.ai_explanation is not None
    assert db_exc.status == "OPEN"

    # Verify AuditLog row was added
    audit = db_session.query(AuditLog).filter(
        AuditLog.entity_id == db_result.reconciliation_id
    ).first()
    assert audit is not None
    assert audit.actor == "AI_CONTROLLER"
    assert audit.action == "AI_REASONED"
    assert audit.new_value == "REVIEW"


# ---------------------------------------------------------------------------
# Test 7: Missing DB Session for Persist Raises ValueError
# ---------------------------------------------------------------------------

def test_orchestration_missing_db_for_persist():
    """Verify calling reconcile_and_investigate(persist=True) without a DB session raises ValueError."""
    controller = FinanceController(db=None)
    gw = make_orch_txn("FC_ORCH_GW_NODB", "GATEWAY", ref_id="pay_nodb", amount=100.0)

    with pytest.raises(ValueError, match="A database session \\(db\\) is required when persist=True."):
        controller.reconcile_and_investigate(transactions=[gw], persist=True)


# ---------------------------------------------------------------------------
# Test 8: Summary Preserves All Original Deterministic Keys
# ---------------------------------------------------------------------------

def test_orchestration_does_not_break_existing_summary():
    """
    Verify all original deterministic reconciliation summary keys are preserved
    alongside the new 'ai_results' key.
    """
    controller = FinanceController()
    gw = make_orch_txn("FC_ORCH_GW_SUM", "GATEWAY", ref_id="pay_sum_test", amount=500.0)
    bnk = make_orch_txn("FC_ORCH_BNK_SUM", "BANK", ref_id="pay_sum_test", amount=500.0)

    summary = controller.reconcile_and_investigate(transactions=[gw, bnk], persist=False)

    expected_keys = {
        "total_clusters",
        "total_reconciled",
        "total_review",
        "total_exceptions",
        "auto_reconciled_rate",
        "results",
        "exceptions",
        "ai_results",
    }
    for key in expected_keys:
        assert key in summary, f"Missing expected key '{key}' in orchestration summary."
