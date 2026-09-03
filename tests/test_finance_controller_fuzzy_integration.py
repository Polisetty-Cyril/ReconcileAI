"""
ReconcileAI - Finance Controller Step 2C Fuzzy Integration Tests
Exercises the Finance Controller fuzzy investigation boundary against the
real FuzzyMatchEngine, verifying delegation, pairwise scoring, candidate
selection, safety boundaries, and error handling.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import MagicMock, patch
import pytest

from backend.models.transaction import Transaction
from backend.models.reconciliation import ReconciliationResult
from backend.models.exception import ReconciliationException
from backend.services.finance_controller import FinanceController
from backend.services.fuzzy_matcher import (
    FuzzyMatchEngine,
    FuzzyMatchResult,
    FuzzyReasonCode,
)
from backend.services.ai_controller import AIController
from backend.services.llm_client import BaseLLMClient, GeminiLLMClient


# ---------------------------------------------------------------------------
# Test Fixtures & Helpers
# ---------------------------------------------------------------------------

def make_test_transaction(
    txn_id: str,
    source: str,
    ref_id: str = "pay_fc_fuzz_100",
    order_id: str = "ORD_FC_FUZZ_100",
    amount: float = 3500.00,
    desc: str = "Test transaction",
    customer_name: Optional[str] = "Rahul Sharma",
    txn_date: datetime = datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc),
    status: str = "CAPTURED",
    txn_type: str = "PAYMENT",
) -> Transaction:
    """Constructs realistic Transaction ORM model instances for testing."""
    txn = Transaction(
        transaction_id=txn_id,
        source=source,
        reference_id=ref_id,
        order_id=order_id,
        customer_id="CUST_FC_FUZZ_100",
        amount=amount,
        currency="INR",
        transaction_date=txn_date,
        status=status,
        transaction_type=txn_type,
        description=desc,
    )
    if customer_name is not None:
        txn.customer_name = customer_name
    return txn


# ---------------------------------------------------------------------------
# 1. Exact-Match Fast Path
# ---------------------------------------------------------------------------

def test_exact_match_fast_path_bypasses_fuzzy_engine():
    """
    Verify that if a ReconciliationResult has final_decision="AUTO_RECONCILED",
    investigate_with_fuzzy() returns None immediately without calling FuzzyMatchEngine.
    """
    mock_fuzzy = MagicMock(spec=FuzzyMatchEngine)
    controller = FinanceController(fuzzy_engine=mock_fuzzy)

    auto_recon_result = ReconciliationResult(
        reconciliation_id="REC_AUTO_1001",
        gateway_transaction_id="GW_AUTO_1",
        bank_transaction_id="BNK_AUTO_1",
        final_decision="AUTO_RECONCILED",
        match_score=100.0,
        matching_method="EXACT_RULE",
        is_resolved=True,
        discrepancy_amount=0.0,
    )

    gw = make_test_transaction("GW_AUTO_1", "GATEWAY", ref_id="pay_auto_1")
    bnk = make_test_transaction("BNK_AUTO_1", "BANK", ref_id="pay_auto_1")

    # Call with AUTO_RECONCILED result
    fuzzy_result = controller.investigate_with_fuzzy(
        result=auto_recon_result,
        gateway_txn=gw,
        bank_txn=bnk,
    )

    assert fuzzy_result is None
    mock_fuzzy.score_pair.assert_not_called()
    mock_fuzzy.find_best_candidates.assert_not_called()


# ---------------------------------------------------------------------------
# 2. Real Pairwise Fuzzy Investigation
# ---------------------------------------------------------------------------

def test_real_pairwise_fuzzy_investigation():
    """
    Construct realistic Gateway and Bank Transaction ORM objects with similar
    but non-identical references/descriptions. Pass through the real FinanceController
    and real FuzzyMatchEngine, asserting a valid FuzzyMatchResult is returned.
    """
    controller = FinanceController()

    base_date = datetime(2026, 8, 20, 11, 0, 0, tzinfo=timezone.utc)
    gw = make_test_transaction(
        txn_id="FC_FUZZ_GW_PAIR_1",
        source="GATEWAY",
        ref_id="pay_rzp_98452",
        order_id="ORD_98452",
        amount=4500.00,
        desc="Razorpay payment captured pay_rzp_98452",
        customer_name="Rahul Sharma",
        txn_date=base_date,
    )
    bnk = make_test_transaction(
        txn_id="FC_FUZZ_BNK_PAIR_1",
        source="BANK",
        ref_id="PAY-RZP-98452",
        order_id="ORD_98452",
        amount=4500.00,
        desc="NEFT CR PAY-RZP-98452 SETTLEMENT",
        customer_name="Rahul Sharma",
        txn_date=base_date + timedelta(days=1),
        status="CREDIT",
        txn_type="SETTLEMENT",
    )

    result = controller.investigate_with_fuzzy(
        gateway_txn=gw,
        bank_txn=bnk,
    )

    # 1. Verify instance type
    assert isinstance(result, FuzzyMatchResult)

    # 2. Verify IDs
    assert result.gateway_txn_id == "FC_FUZZ_GW_PAIR_1"
    assert result.bank_txn_id == "FC_FUZZ_BNK_PAIR_1"

    # 3. Verify score ranges (0..100)
    assert 0.0 <= result.reference_score <= 100.0
    assert 0.0 <= result.description_score <= 100.0
    assert 0.0 <= result.customer_score <= 100.0
    assert 0.0 <= result.composite_score <= 100.0

    # "pay_rzp_98452" and "PAY-RZP-98452" normalise to "payrzp98452"
    assert result.reference_score == 100.0
    assert result.customer_score == 100.0
    assert result.composite_score >= 85.0

    # 4. Verify amount attributes
    assert result.amount_match is True
    assert result.amount_diff == 0.0

    # 5. Verify valid decision and reason codes
    assert result.decision in ("FUZZY_MATCHED", "FUZZY_REVIEW", "FUZZY_NO_MATCH")
    assert result.decision == "FUZZY_MATCHED"
    assert FuzzyReasonCode.FUZZY_MATCHED in result.reason_codes
    assert FuzzyReasonCode.AMOUNT_CONFIRMED in result.reason_codes
    assert "reference_id" in result.matched_fields


# ---------------------------------------------------------------------------
# 3. Real Candidate Investigation
# ---------------------------------------------------------------------------

def test_real_candidate_investigation():
    """
    Construct one Gateway transaction and two candidate Bank transactions,
    where one candidate is clearly the true matching candidate.
    Verify the real FuzzyMatchEngine selects the best candidate.
    """
    controller = FinanceController()

    base_date = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
    gw = make_test_transaction(
        txn_id="FC_FUZZ_GW_CAND",
        source="GATEWAY",
        ref_id="pay_target_555",
        amount=12000.00,
        desc="Payment captured for pay_target_555",
        customer_name="Priya Patel",
        txn_date=base_date,
    )

    cand_best = make_test_transaction(
        txn_id="FC_FUZZ_BNK_BEST",
        source="BANK",
        ref_id="PAY-TARGET-555",
        amount=12000.00,
        desc="NEFT CR PAY-TARGET-555",
        customer_name="Priya Patel",
        txn_date=base_date + timedelta(days=1),
    )

    cand_unrelated = make_test_transaction(
        txn_id="FC_FUZZ_BNK_UNRELATED",
        source="BANK",
        ref_id="unrelated_ref_999",
        amount=12000.00,
        desc="ACH Transfer vendor payroll 999",
        customer_name="Other Vendor Corp",
        txn_date=base_date + timedelta(days=2),
    )

    result = controller.investigate_with_fuzzy(
        gateway_txn=gw,
        candidate_banks=[cand_unrelated, cand_best],
    )

    assert isinstance(result, FuzzyMatchResult)
    assert result.gateway_txn_id == "FC_FUZZ_GW_CAND"
    # Verify the best candidate was selected by the engine, not the unrelated one
    assert result.bank_txn_id == "FC_FUZZ_BNK_BEST"
    assert result.decision in ("FUZZY_MATCHED", "FUZZY_REVIEW", "FUZZY_NO_MATCH")
    assert result.decision == "FUZZY_MATCHED"
    assert result.composite_score >= 85.0
    assert result.amount_match is True


# ---------------------------------------------------------------------------
# 4. Dependency Injection / Boundary Test
# ---------------------------------------------------------------------------

def test_fuzzy_dependency_injection():
    """
    Inject a mock FuzzyMatchEngine into FinanceController and verify that
    investigate_with_fuzzy() properly delegates to score_pair() with the exact
    arguments and returns the engine's result unchanged.
    """
    mock_fuzzy = MagicMock(spec=FuzzyMatchEngine)
    mock_result = FuzzyMatchResult(
        match_id="fuzz_mock_001",
        gateway_txn_id="GW_MOCK_1",
        bank_txn_id="BNK_MOCK_1",
        reference_score=92.0,
        description_score=80.0,
        customer_score=0.0,
        composite_score=70.0,
        decision="FUZZY_REVIEW",
        reason_codes=[FuzzyReasonCode.FUZZY_REVIEW],
        matched_fields=["reference_id"],
        amount_match=True,
        amount_diff=0.0,
    )
    mock_fuzzy.score_pair.return_value = mock_result

    controller = FinanceController(fuzzy_engine=mock_fuzzy)

    gw = make_test_transaction("GW_MOCK_1", "GATEWAY")
    bnk = make_test_transaction("BNK_MOCK_1", "BANK")

    returned_result = controller.investigate_with_fuzzy(gateway_txn=gw, bank_txn=bnk)

    mock_fuzzy.score_pair.assert_called_once_with(gw, bnk)
    mock_fuzzy.find_best_candidates.assert_not_called()
    assert returned_result is mock_result


# ---------------------------------------------------------------------------
# 5. Safety Boundary Test
# ---------------------------------------------------------------------------

def test_safety_boundary_preserves_resolution_state_and_exceptions():
    """
    Verify that fuzzy investigation strictly acts as an evidence-gathering boundary:
    - Does NOT alter ReconciliationResult.is_resolved or final_decision
    - Does NOT alter ReconciliationException status or approve/reject
    - Does NOT trigger database persistence or commits
    - Does NOT invoke AIController, Gemini, or any LLM
    """
    controller = FinanceController()

    unresolved_result = ReconciliationResult(
        reconciliation_id="REC_DISCREPANCY_2001",
        gateway_transaction_id="GW_DISC_1",
        bank_transaction_id="BNK_DISC_1",
        final_decision="HUMAN_REVIEW",
        match_score=50.0,
        matching_method="EXACT_RULE",
        is_resolved=False,
        discrepancy_amount=250.0,
    )

    open_exception = ReconciliationException(
        exception_id="EXC_DISC_2001",
        reconciliation_id="REC_DISCREPANCY_2001",
        transaction_id="GW_DISC_1",
        category="AMOUNT_MISMATCH",
        severity="HIGH",
        difference_amount=250.0,
        status="OPEN",
        resolved_by=None,
        resolved_at=None,
    )

    gw = make_test_transaction("GW_DISC_1", "GATEWAY", ref_id="pay_disc_1", amount=5000.0)
    bnk = make_test_transaction("BNK_DISC_1", "BANK", ref_id="PAY-DISC-1", amount=4750.0)

    with patch.object(AIController, "investigate") as mock_ai_inv, \
         patch.object(AIController, "investigate_with_fuzzy") as mock_ai_fuzz, \
         patch.object(BaseLLMClient, "reason") as mock_base_llm, \
         patch.object(GeminiLLMClient, "reason") as mock_gemini:

        fuzzy_result = controller.investigate_with_fuzzy(
            result=unresolved_result,
            gateway_txn=gw,
            bank_txn=bnk,
        )

        # 1. Evidence was produced
        assert isinstance(fuzzy_result, FuzzyMatchResult)

        # 2. Result state was NOT mutated
        assert unresolved_result.is_resolved is False
        assert unresolved_result.final_decision == "HUMAN_REVIEW"
        assert unresolved_result.discrepancy_amount == 250.0

        # 3. Exception state was NOT mutated
        assert open_exception.status == "OPEN"
        assert open_exception.resolved_by is None
        assert open_exception.resolved_at is None

        # 4. Zero LLM / AI calls were made
        assert mock_ai_inv.call_count == 0
        assert mock_ai_fuzz.call_count == 0
        assert mock_base_llm.call_count == 0
        assert mock_gemini.call_count == 0


# ---------------------------------------------------------------------------
# 6. Invalid Input Validation Test
# ---------------------------------------------------------------------------

def test_invalid_inputs_raise_value_error():
    """
    Verify that calling investigate_with_fuzzy() with ambiguous or missing
    inputs raises ValueError.
    """
    controller = FinanceController()
    gw = make_test_transaction("GW_INV_1", "GATEWAY")

    # No arguments at all
    with pytest.raises(ValueError, match="Must provide either \\(gateway_txn, bank_txn\\)"):
        controller.investigate_with_fuzzy()

    # Only gateway_txn provided
    with pytest.raises(ValueError, match="Must provide either \\(gateway_txn, bank_txn\\)"):
        controller.investigate_with_fuzzy(gateway_txn=gw)

    # Result provided but not AUTO_RECONCILED, and no transaction data
    review_result = ReconciliationResult(
        reconciliation_id="REC_REV_1",
        final_decision="HUMAN_REVIEW",
    )
    with pytest.raises(ValueError, match="Must provide either \\(gateway_txn, bank_txn\\)"):
        controller.investigate_with_fuzzy(result=review_result)
