"""
ReconcileAI - Finance Controller Step 3B AI Reasoning Integration Tests
Integration test suite verifying the Finance Controller boundary to AIController:
1. Injected AIController dependency injection
2. Unresolved ReconciliationResult flow to injected LLM client
3. Automatic fuzzy evidence forwarding when gateway/bank transactions supplied
4. AUTO_RECONCILED fast-path bypass
5. persist=False does not write to database
6. persist=True calls AIController.persist_result() without committing
7. Safety boundary: advisory-only, is_resolved=False, status=OPEN, amounts unchanged
8. Missing DB session with persist=True raises ValueError
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
from backend.services.fuzzy_matcher import (
    FuzzyMatchEngine,
    FuzzyMatchResult,
    FuzzyReasonCode,
)
from backend.services.ai_controller import AIController
from backend.services.llm_client import BaseLLMClient


# ---------------------------------------------------------------------------
# Test Fixtures & Helpers
# ---------------------------------------------------------------------------

def cleanup_fc_ai_test_records(db: Session) -> None:
    """Safely removes any records created with FC_AI_ identifiers."""
    db.query(AuditLog).filter(
        (AuditLog.audit_id.like("AUD_AI_%")) | (AuditLog.entity_id.like("REC_FC_AI_%"))
    ).delete(synchronize_session=False)

    db.query(ReconciliationException).filter(
        (ReconciliationException.exception_id.like("EXC_FC_AI_%")) |
        (ReconciliationException.reconciliation_id.like("REC_FC_AI_%")) |
        (ReconciliationException.transaction_id.like("FC_AI_%"))
    ).delete(synchronize_session=False)

    db.query(ReconciliationResult).filter(
        (ReconciliationResult.reconciliation_id.like("REC_FC_AI_%")) |
        (ReconciliationResult.gateway_transaction_id.like("FC_AI_%")) |
        (ReconciliationResult.bank_transaction_id.like("FC_AI_%"))
    ).delete(synchronize_session=False)

    db.query(Transaction).filter(
        Transaction.transaction_id.like("FC_AI_%")
    ).delete(synchronize_session=False)

    db.commit()


@pytest.fixture(scope="module", autouse=True)
def setup_ai_test_db():
    """Initializes schema and cleans up test records before and after test module."""
    init_db()
    db: Session = SessionLocal()
    try:
        cleanup_fc_ai_test_records(db)
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        cleanup_fc_ai_test_records(db)
    finally:
        db.close()


@pytest.fixture
def db_session():
    """Provides a transactional database session for tests with automatic cleanup."""
    db: Session = SessionLocal()
    try:
        cleanup_fc_ai_test_records(db)
        yield db
    finally:
        cleanup_fc_ai_test_records(db)
        db.close()


class MockLLMClient(BaseLLMClient):
    """Deterministic mock client that records evidence and returns configured responses."""

    def __init__(self, return_dict: Optional[Dict[str, Any]] = None) -> None:
        self.recorded_evidence: List[Dict[str, Any]] = []
        self.call_count: int = 0
        self.return_dict = return_dict or {
            "recommendation": "REVIEW",
            "confidence": 0.85,
            "reason": "Mock LLM reasoning statement.",
            "risk": "MEDIUM",
        }

    def reason(self, evidence: Dict[str, Any]) -> Dict[str, Any]:
        self.call_count += 1
        self.recorded_evidence.append(evidence)
        return self.return_dict


def make_test_txn(
    txn_id: str,
    source: str,
    ref_id: str = "pay_fc_ai_100",
    amount: float = 5000.0,
    desc: str = "Payment",
    customer_name: str = "Aarav Gupta",
) -> Transaction:
    """Helper to construct realistic Transaction ORM model instances."""
    txn = Transaction(
        transaction_id=txn_id,
        source=source,
        reference_id=ref_id,
        order_id="ORD_FC_AI_100",
        customer_id="CUST_FC_AI_100",
        amount=amount,
        currency="INR",
        transaction_date=datetime(2026, 8, 25, 10, 0, 0, tzinfo=timezone.utc),
        status="CAPTURED",
        transaction_type="PAYMENT",
        description=desc,
    )
    txn.customer_name = customer_name
    return txn


# ---------------------------------------------------------------------------
# Test 1: Injected AIController Dependency Injection
# ---------------------------------------------------------------------------

def test_1_finance_controller_accepts_injected_ai_controller():
    """Verify FinanceController accepts an injected AIController instance."""
    mock_ai = MagicMock(spec=AIController)
    controller = FinanceController(ai_controller=mock_ai)
    assert controller.ai_controller is mock_ai


# ---------------------------------------------------------------------------
# Test 2: Unresolved Result Flows Through Controller -> AIController -> LLM
# ---------------------------------------------------------------------------

def test_2_unresolved_result_flows_to_ai_controller_and_llm():
    """
    Verify an unresolved ReconciliationResult flows through:
    FinanceController -> AIController -> Injected Mock LLM Client
    and returns a validated AIControllerResult.
    """
    mock_client = MockLLMClient({
        "recommendation": "ESCALATE",
        "confidence": 0.92,
        "reason": "Material discrepancy detected requiring senior accountant review.",
        "risk": "HIGH",
    })
    ai_controller = AIController(client=mock_client)
    controller = FinanceController(ai_controller=ai_controller)

    result = ReconciliationResult(
        reconciliation_id="REC_FC_AI_001",
        gateway_transaction_id="GW_FC_AI_001",
        bank_transaction_id="BNK_FC_AI_001",
        final_decision="HUMAN_REVIEW",
        match_score=60.0,
        matching_method="EXACT_RULE",
        is_resolved=False,
        discrepancy_amount=500.0,
    )
    exception = ReconciliationException(
        exception_id="EXC_FC_AI_001",
        reconciliation_id="REC_FC_AI_001",
        transaction_id="GW_FC_AI_001",
        category="AMOUNT_MISMATCH",
        severity="HIGH",
        difference_amount=500.0,
        status="OPEN",
    )

    ai_result = controller.investigate_with_ai(result=result, exception=exception)

    # 1. Verify returned result
    assert isinstance(ai_result, AIControllerResult)
    assert ai_result.recommendation == "ESCALATE"
    assert ai_result.confidence == 0.92
    assert ai_result.risk == "HIGH"
    assert "Material discrepancy" in ai_result.reason

    # 2. Verify LLM client was invoked with proper evidence
    assert mock_client.call_count == 1
    evidence = mock_client.recorded_evidence[0]
    assert evidence["reconciliation_id"] == "REC_FC_AI_001"
    assert evidence["category"] == "AMOUNT_MISMATCH"
    assert evidence["difference_amount"] == 500.0


# ---------------------------------------------------------------------------
# Test 3: Fuzzy Evidence Forwarded to AIController
# ---------------------------------------------------------------------------

def test_3_fuzzy_evidence_forwarded_when_transactions_supplied():
    """
    Verify that when gateway/bank transactions are supplied, FinanceController
    runs fuzzy investigation on-demand and passes the FuzzyMatchResult to AIController.
    """
    mock_client = MockLLMClient({
        "recommendation": "REVIEW",
        "confidence": 0.88,
        "reason": "High fuzzy similarity between reference IDs indicates possible typo.",
        "risk": "MEDIUM",
    })
    ai_controller = AIController(client=mock_client)
    controller = FinanceController(ai_controller=ai_controller)

    result = ReconciliationResult(
        reconciliation_id="REC_FC_AI_002",
        gateway_transaction_id="GW_FC_AI_002",
        bank_transaction_id="BNK_FC_AI_002",
        final_decision="HUMAN_REVIEW",
        match_score=70.0,
        is_resolved=False,
        discrepancy_amount=0.0,
    )
    gw = make_test_txn("GW_FC_AI_002", "GATEWAY", ref_id="pay_998877", amount=2500.0)
    bnk = make_test_txn("BNK_FC_AI_002", "BANK", ref_id="PAY-998877", amount=2500.0)

    ai_result = controller.investigate_with_ai(
        result=result,
        gateway_txn=gw,
        bank_txn=bnk,
    )

    assert isinstance(ai_result, AIControllerResult)
    assert mock_client.call_count == 1
    evidence = mock_client.recorded_evidence[0]

    # Confirm fuzzy evidence was provided to the LLM client
    assert "fuzzy_decision" in evidence
    assert evidence["fuzzy_decision"] == "FUZZY_MATCHED"
    assert evidence["fuzzy_composite_score"] >= 85.0
    assert evidence["fuzzy_amount_diff"] == 0.0


# ---------------------------------------------------------------------------
# Test 4: AUTO_RECONCILED Fast-Path Bypasses Fuzzy and LLM
# ---------------------------------------------------------------------------

def test_4_auto_reconciled_fast_path_bypasses_fuzzy_and_llm():
    """
    Verify that an AUTO_RECONCILED result immediately returns the deterministic
    outcome, bypassing fuzzy matching and making zero LLM calls.
    """
    mock_client = MockLLMClient()
    mock_fuzzy = MagicMock(spec=FuzzyMatchEngine)
    ai_controller = AIController(client=mock_client, fuzzy_engine=mock_fuzzy)
    controller = FinanceController(fuzzy_engine=mock_fuzzy, ai_controller=ai_controller)

    auto_result = ReconciliationResult(
        reconciliation_id="REC_FC_AI_AUTO",
        gateway_transaction_id="GW_FC_AI_AUTO",
        bank_transaction_id="BNK_FC_AI_AUTO",
        final_decision="AUTO_RECONCILED",
        match_score=100.0,
        is_resolved=True,
        discrepancy_amount=0.0,
    )
    gw = make_test_txn("GW_FC_AI_AUTO", "GATEWAY")
    bnk = make_test_txn("BNK_FC_AI_AUTO", "BANK")

    ai_result = controller.investigate_with_ai(
        result=auto_result,
        gateway_txn=gw,
        bank_txn=bnk,
    )

    assert ai_result.recommendation == "AUTO_RECONCILE"
    assert ai_result.confidence == 1.0
    assert ai_result.risk == "LOW"

    # ZERO fuzzy calls and ZERO LLM calls
    mock_fuzzy.score_pair.assert_not_called()
    mock_fuzzy.find_best_candidates.assert_not_called()
    assert mock_client.call_count == 0


# ---------------------------------------------------------------------------
# Test 5: persist=False Does Not Write to Database
# ---------------------------------------------------------------------------

def test_5_persist_false_does_not_modify_database(db_session: Session):
    """
    Verify that investigate_with_ai() with persist=False does not write AI fields
    or create AuditLog entries in the database.
    """
    mock_client = MockLLMClient()
    ai_controller = AIController(client=mock_client)
    controller = FinanceController(db=db_session, ai_controller=ai_controller)

    result = ReconciliationResult(
        reconciliation_id="REC_FC_AI_NP_1",
        gateway_transaction_id="GW_FC_AI_NP_1",
        final_decision="HUMAN_REVIEW",
        match_score=75.0,
        matching_method="EXACT_RULE",
        is_resolved=False,
        discrepancy_amount=100.0,
    )
    db_session.add(result)
    db_session.commit()

    controller.investigate_with_ai(result=result, persist=False)

    # Re-query result from database
    refreshed = db_session.query(ReconciliationResult).filter(
        ReconciliationResult.reconciliation_id == "REC_FC_AI_NP_1"
    ).first()
    assert refreshed.ai_recommendation is None
    assert refreshed.ai_confidence is None
    assert refreshed.ai_reasoning is None

    # Verify no audit log was created
    audits = db_session.query(AuditLog).filter(
        AuditLog.entity_id == "REC_FC_AI_NP_1"
    ).all()
    assert len(audits) == 0


# ---------------------------------------------------------------------------
# Test 6: persist=True Calls persist_result() Without Committing
# ---------------------------------------------------------------------------

def test_6_persist_true_writes_ai_fields_without_committing(db_session: Session):
    """
    Verify that persist=True calls AIController.persist_result(), writes AI columns
    and an AuditLog entry to the session, but does NOT commit inside FinanceController.
    """
    mock_client = MockLLMClient({
        "recommendation": "REVIEW",
        "confidence": 0.82,
        "reason": "Timing discrepancy between gateway capture and bank settlement.",
        "risk": "MEDIUM",
    })
    ai_controller = AIController(client=mock_client)
    controller = FinanceController(ai_controller=ai_controller)

    result = ReconciliationResult(
        reconciliation_id="REC_FC_AI_P_1",
        gateway_transaction_id="GW_FC_AI_P_1",
        final_decision="HUMAN_REVIEW",
        match_score=75.0,
        matching_method="EXACT_RULE",
        is_resolved=False,
        discrepancy_amount=200.0,
    )
    exception = ReconciliationException(
        exception_id="EXC_FC_AI_P_1",
        reconciliation_id="REC_FC_AI_P_1",
        transaction_id="GW_FC_AI_P_1",
        category="DATE_MISMATCH",
        severity="MEDIUM",
        status="OPEN",
    )
    db_session.add_all([result, exception])
    db_session.commit()

    # Call with persist=True and spy on session.commit
    with patch.object(db_session, "commit") as mock_commit:
        ai_result = controller.investigate_with_ai(
            result=result,
            exception=exception,
            persist=True,
            db=db_session,
        )
        # FinanceController must NOT commit
        mock_commit.assert_not_called()

    # Verify fields were populated in the session
    assert result.ai_recommendation == "REVIEW"
    assert result.ai_confidence == 82.0
    assert "Timing discrepancy" in result.ai_reasoning
    assert exception.ai_explanation == ai_result.reason

    # Commit explicitly by caller
    db_session.commit()

    # Verify audit log was persisted
    audit = db_session.query(AuditLog).filter(
        AuditLog.entity_id == "REC_FC_AI_P_1"
    ).first()
    assert audit is not None
    assert audit.actor == "AI_CONTROLLER"
    assert audit.action == "AI_REASONED"
    assert audit.new_value == "REVIEW"


# ---------------------------------------------------------------------------
# Test 7: Safety Boundary - AI Cannot Resolve or Approve Exception
# ---------------------------------------------------------------------------

def test_7_safety_boundary_ai_cannot_resolve_or_approve_exception():
    """
    Verify the fundamental safety boundary:
    Even if the LLM recommends AUTO_RECONCILE or REVIEW:
    - result.is_resolved MUST remain False
    - exception.status MUST remain OPEN
    - transaction amounts MUST remain untouched
    - AI remains strictly advisory
    """
    mock_client = MockLLMClient({
        "recommendation": "AUTO_RECONCILE",
        "confidence": 0.99,
        "reason": "AI strongly believes this should be resolved.",
        "risk": "LOW",
    })
    ai_controller = AIController(client=mock_client)
    controller = FinanceController(ai_controller=ai_controller)

    result = ReconciliationResult(
        reconciliation_id="REC_FC_AI_SAFE",
        gateway_transaction_id="GW_FC_AI_SAFE",
        bank_transaction_id="BNK_FC_AI_SAFE",
        final_decision="HUMAN_REVIEW",
        match_score=75.0,
        is_resolved=False,
        discrepancy_amount=150.0,
    )
    exception = ReconciliationException(
        exception_id="EXC_FC_AI_SAFE",
        reconciliation_id="REC_FC_AI_SAFE",
        transaction_id="GW_FC_AI_SAFE",
        category="AMOUNT_MISMATCH",
        severity="MEDIUM",
        difference_amount=150.0,
        status="OPEN",
        resolved_by=None,
        resolved_at=None,
    )
    gw = make_test_txn("GW_FC_AI_SAFE", "GATEWAY", amount=5000.0)
    bnk = make_test_txn("BNK_FC_AI_SAFE", "BANK", amount=4850.0)

    ai_result = controller.investigate_with_ai(
        result=result,
        exception=exception,
        gateway_txn=gw,
        bank_txn=bnk,
    )

    # 1. AI recommended AUTO_RECONCILE
    assert ai_result.recommendation == "AUTO_RECONCILE"

    # 2. But ReconciliationResult MUST NOT be marked resolved
    assert result.is_resolved is False
    assert result.final_decision == "HUMAN_REVIEW"

    # 3. Exception MUST NOT be approved or resolved
    assert exception.status == "OPEN"
    assert exception.status not in ("APPROVED", "REJECTED", "RESOLVED")
    assert exception.resolved_by is None
    assert exception.resolved_at is None

    # 4. Financial amounts remain completely untouched
    assert gw.amount == 5000.0
    assert bnk.amount == 4850.0


# ---------------------------------------------------------------------------
# Test 8: Missing DB Session With persist=True Raises ValueError
# ---------------------------------------------------------------------------

def test_8_missing_db_session_with_persist_raises_value_error():
    """Verify that calling investigate_with_ai() with persist=True and no DB raises ValueError."""
    mock_client = MockLLMClient()
    ai_controller = AIController(client=mock_client)
    controller = FinanceController(db=None, ai_controller=ai_controller)

    result = ReconciliationResult(
        reconciliation_id="REC_FC_AI_NODB",
        final_decision="HUMAN_REVIEW",
    )

    with pytest.raises(ValueError, match="A database session \\(db\\) is required when persist=True."):
        controller.investigate_with_ai(result=result, persist=True)


def test_9_missing_result_raises_value_error():
    """Verify that calling investigate_with_ai() with result=None raises ValueError."""
    controller = FinanceController()
    with pytest.raises(ValueError, match="ReconciliationResult must be provided"):
        controller.investigate_with_ai(result=None)  # type: ignore[arg-type]
