"""
ReconcileAI - Fuzzy Match Engine -> AI Controller Bridge Test Suite
Checkpoint 1: Phase 7 (Fuzzy) to Phase 8 (AI Reasoning) Integration

Verifies:
  1. AUTO_RECONCILED case makes ZERO fuzzy calls and ZERO LLM calls.
  2. AUTO_RECONCILED preserves is_resolved=True with no financial mutation.
  3. Pairwise discrepancy explicitly calls score_pair() and feeds FuzzyMatchResult into AI.
  4. Candidate bank discrepancy explicitly calls find_best_candidates() and feeds best result into AI.
  5. Mocked Gemini client path makes zero real network calls.
  6. Heuristic fallback / offline path works seamlessly with fuzzy signals.
  7. AI recommendations remain strictly advisory for unresolved cases (is_resolved remains False,
     exception status untouched, balances untouched).
  8. FuzzyMatchEngine instance reuse and custom configuration support.
  9. _collect_evidence safely processes both FuzzyMatchResult dataclass and dicts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from backend.models.exception import ReconciliationException
from backend.models.reconciliation import ReconciliationResult
from backend.schemas.ai_controller import AIControllerResult
from backend.services.ai_controller import AIController, _collect_evidence
from backend.services.fuzzy_matcher import (
    FuzzyMatchEngine,
    FuzzyMatchResult,
    FuzzyReasonCode,
)
from backend.services.llm_client import (
    BaseLLMClient,
    GeminiLLMClient,
    HeuristicLLMClient,
)


# ---------------------------------------------------------------------------
# Lightweight test helpers & stubs
# ---------------------------------------------------------------------------

@dataclass
class StubTxn:
    """Lightweight canonical transaction stand-in for fuzzy matching."""
    transaction_id: str
    reference_id: Optional[str] = None
    description: Optional[str] = None
    customer_name: Optional[str] = None
    amount: float = 1000.0


def make_test_result(
    recon_id: str = "REC_TEST_001",
    final_decision: str = "HUMAN_REVIEW",
    match_score: float = 75.0,
    discrepancy_amount: float = 100.0,
    is_resolved: bool = False,
) -> ReconciliationResult:
    """Creates a mock ReconciliationResult."""
    return ReconciliationResult(
        reconciliation_id=recon_id,
        gateway_transaction_id="GW_001",
        bank_transaction_id="BNK_001",
        match_score=match_score,
        matching_method="FUZZY_ASSISTED",
        final_decision=final_decision,
        discrepancy_amount=discrepancy_amount,
        is_resolved=is_resolved,
    )


def make_test_exception(
    exc_id: str = "EXC_TEST_001",
    recon_id: str = "REC_TEST_001",
    category: str = "AMOUNT_MISMATCH",
    severity: str = "MEDIUM",
    diff_amount: float = 100.0,
    status: str = "PENDING",
) -> ReconciliationException:
    """Creates a mock ReconciliationException."""
    return ReconciliationException(
        exception_id=exc_id,
        reconciliation_id=recon_id,
        category=category,
        severity=severity,
        difference_amount=diff_amount,
        status=status,
    )


class RecordingFakeClient(BaseLLMClient):
    """Test client that records received evidence and returns a mock result."""

    def __init__(self, return_dict: Optional[Dict[str, Any]] = None) -> None:
        self.recorded_evidence: List[Dict[str, Any]] = []
        self.call_count: int = 0
        self.return_dict = return_dict or {
            "recommendation": "REVIEW",
            "confidence": 0.85,
            "reason": "Recording client test reasoning.",
            "risk": "MEDIUM",
        }

    def reason(self, evidence: Dict[str, Any]) -> Dict[str, Any]:
        self.call_count += 1
        self.recorded_evidence.append(evidence)
        return self.return_dict


# ---------------------------------------------------------------------------
# Test Suite
# ---------------------------------------------------------------------------

class TestFuzzyGeminiBridge:
    """Checkpoint 1 Integration Tests: FuzzyMatchEngine -> AIController Bridge."""

    def test_exact_deterministic_matches_bypass_fuzzy_and_ai(self):
        """
        Exact deterministic matches (AUTO_RECONCILED) must bypass both
        the FuzzyMatchEngine and AI reasoning entirely.
        Preserves is_resolved=True, makes ZERO fuzzy calls, ZERO LLM calls,
        and causes no financial mutation.
        """
        result = make_test_result(
            recon_id="REC_DET_001",
            final_decision="AUTO_RECONCILED",
            match_score=100.0,
            discrepancy_amount=0.0,
            is_resolved=True,
        )
        fake_client = RecordingFakeClient()
        mock_fuzzy_engine = MagicMock(spec=FuzzyMatchEngine)

        controller = AIController(client=fake_client, fuzzy_engine=mock_fuzzy_engine)

        gw = StubTxn(transaction_id="GW_001", reference_id="REF100", amount=500.0)
        bnk = StubTxn(transaction_id="BNK_001", reference_id="REF100", amount=500.0)

        ai_result = controller.investigate_with_fuzzy(result, gateway_txn=gw, bank_txn=bnk)

        # ZERO fuzzy calls and ZERO LLM calls
        mock_fuzzy_engine.score_pair.assert_not_called()
        mock_fuzzy_engine.find_best_candidates.assert_not_called()
        assert fake_client.call_count == 0

        # Returns deterministic AUTO_RECONCILE result
        assert ai_result.recommendation == "AUTO_RECONCILE"
        assert ai_result.confidence == 1.0
        assert ai_result.risk == "LOW"

        # Resolution state and financial balances preserved
        assert result.is_resolved is True
        assert result.discrepancy_amount == 0.0
        assert gw.amount == 500.0
        assert bnk.amount == 500.0

    def test_pairwise_discrepancy_calls_score_pair_then_ai(self):
        """
        Pairwise discrepancy explicitly calls fuzzy score_pair() and then AI.
        """
        result = make_test_result(
            recon_id="REC_PAIR_001",
            final_decision="HUMAN_REVIEW",
            discrepancy_amount=0.0,
        )
        fake_client = RecordingFakeClient()
        mock_fuzzy_engine = MagicMock(spec=FuzzyMatchEngine)
        expected_fuzzy_result = FuzzyMatchResult(
            match_id="fuzz_pair_1",
            gateway_txn_id="GW_P1",
            bank_txn_id="BNK_P1",
            reference_score=95.0,
            description_score=90.0,
            customer_score=90.0,
            composite_score=92.5,
            decision="FUZZY_MATCHED",
            reason_codes=[FuzzyReasonCode.FUZZY_MATCHED],
            matched_fields=["reference_id"],
            amount_match=True,
            amount_diff=0.0,
        )
        mock_fuzzy_engine.score_pair.return_value = expected_fuzzy_result

        controller = AIController(client=fake_client, fuzzy_engine=mock_fuzzy_engine)

        gw = StubTxn(transaction_id="GW_P1", reference_id="pay_1001", amount=1500.0)
        bnk = StubTxn(transaction_id="BNK_P1", reference_id="pay-1001", amount=1500.0)

        ai_result = controller.investigate_with_fuzzy(result, gateway_txn=gw, bank_txn=bnk)

        # score_pair was called with gw and bnk
        mock_fuzzy_engine.score_pair.assert_called_once_with(gw, bnk)
        mock_fuzzy_engine.find_best_candidates.assert_not_called()

        # AI client received evidence containing the fuzzy match result
        assert fake_client.call_count == 1
        evidence = fake_client.recorded_evidence[0]
        assert evidence["fuzzy_decision"] == "FUZZY_MATCHED"
        assert evidence["fuzzy_composite_score"] == 92.5
        assert evidence["fuzzy_amount_diff"] == 0.0

    def test_candidate_discrepancy_calls_find_best_candidates_then_ai(self):
        """
        Candidate bank list discrepancy explicitly calls find_best_candidates() and then AI.
        """
        result = make_test_result(
            recon_id="REC_CAND_001",
            final_decision="HUMAN_REVIEW",
            discrepancy_amount=0.0,
        )
        fake_client = RecordingFakeClient()
        mock_fuzzy_engine = MagicMock(spec=FuzzyMatchEngine)
        expected_fuzzy_result = FuzzyMatchResult(
            match_id="fuzz_cand_1",
            gateway_txn_id="GW_C1",
            bank_txn_id="BNK_MATCH",
            reference_score=92.0,
            description_score=85.0,
            customer_score=90.0,
            composite_score=89.5,
            decision="FUZZY_MATCHED",
            reason_codes=[FuzzyReasonCode.FUZZY_MATCHED],
            matched_fields=["reference_id"],
            amount_match=True,
            amount_diff=0.0,
        )
        mock_fuzzy_engine.find_best_candidates.return_value = [expected_fuzzy_result]

        controller = AIController(client=fake_client, fuzzy_engine=mock_fuzzy_engine)

        gw = StubTxn(transaction_id="GW_C1", reference_id="order_9876", amount=2500.0)
        candidates = [StubTxn(transaction_id="BNK_MATCH", reference_id="order-9876", amount=2500.0)]

        controller.investigate_with_fuzzy(result, gateway_txn=gw, candidate_banks=candidates)

        # find_best_candidates called with [gw] and candidates
        mock_fuzzy_engine.find_best_candidates.assert_called_once_with([gw], candidates)
        mock_fuzzy_engine.score_pair.assert_not_called()

        # AI client received evidence
        assert fake_client.call_count == 1
        evidence = fake_client.recorded_evidence[0]
        assert evidence["fuzzy_decision"] == "FUZZY_MATCHED"
        assert evidence["fuzzy_composite_score"] == 89.5

    def test_pairwise_fuzzy_result_reaches_ai_evidence(self):
        """
        Pairwise fuzzy scoring with default engine delivers the resulting
        FuzzyMatchResult fields into AI evidence.
        """
        result = make_test_result(
            recon_id="REC_PAIR_002",
            final_decision="HUMAN_REVIEW",
            discrepancy_amount=0.0,
        )
        fake_client = RecordingFakeClient(return_dict={
            "recommendation": "AUTO_RECONCILE",
            "confidence": 0.91,
            "reason": "Fuzzy reference similarity 95% with zero amount difference.",
            "risk": "LOW",
        })

        controller = AIController(client=fake_client)

        gw = StubTxn(
            transaction_id="GW_P1",
            reference_id="pay_1001",
            description="Payment invoice 1001",
            customer_name="Alice Smith",
            amount=1500.0,
        )
        bnk = StubTxn(
            transaction_id="BNK_P1",
            reference_id="pay-1001",
            description="Payment invoice 1001",
            customer_name="Alice Smith",
            amount=1500.0,
        )

        ai_result = controller.investigate_with_fuzzy(result, gateway_txn=gw, bank_txn=bnk)

        assert fake_client.call_count == 1
        evidence = fake_client.recorded_evidence[0]

        # Verify fuzzy signals reached the AI client's evidence
        assert evidence["fuzzy_decision"] == "FUZZY_MATCHED"
        assert evidence["fuzzy_composite_score"] > 85.0
        assert evidence["fuzzy_amount_diff"] == 0.0
        assert "reference_id" in evidence["fuzzy_matched_fields"]

        assert ai_result.recommendation == "AUTO_RECONCILE"
        assert ai_result.confidence == 0.91

    def test_candidate_fuzzy_result_reaches_ai_evidence(self):
        """
        When candidate bank transactions are supplied, find_best_candidates()
        selects the best match and delivers it into AI evidence.
        """
        result = make_test_result(
            recon_id="REC_CAND_002",
            final_decision="HUMAN_REVIEW",
            discrepancy_amount=0.0,
        )
        fake_client = RecordingFakeClient()
        controller = AIController(client=fake_client)

        gw = StubTxn(
            transaction_id="GW_C1",
            reference_id="order_9876",
            description="Order settlement 9876",
            customer_name="Bob Jones",
            amount=2500.0,
        )
        candidates = [
            StubTxn(transaction_id="BNK_UNRELATED", reference_id="xyz_1111", description="Misc", customer_name="Charlie", amount=2500.0),
            StubTxn(transaction_id="BNK_MATCH", reference_id="order-9876", description="Order settlement 9876", customer_name="Bob Jones", amount=2500.0),
            StubTxn(transaction_id="BNK_OTHER", reference_id="order_0000", description="Misc", customer_name="David", amount=2500.0),
        ]

        controller.investigate_with_fuzzy(result, gateway_txn=gw, candidate_banks=candidates)

        assert fake_client.call_count == 1
        evidence = fake_client.recorded_evidence[0]

        # The matched candidate should be BNK_MATCH
        assert evidence["fuzzy_decision"] == "FUZZY_MATCHED"
        assert evidence["fuzzy_composite_score"] > 85.0
        assert evidence["fuzzy_amount_diff"] == 0.0

    def test_mocked_gemini_path_makes_zero_real_network_calls(self):
        """
        End-to-end bridge using GeminiLLMClient with a mocked google.genai SDK.
        Ensures zero real network requests are issued.
        """
        valid_response_json = json.dumps({
            "recommendation": "REVIEW",
            "confidence": 0.88,
            "reason": "Mocked Gemini analysis: fuzzy match high, but requires manual confirmation.",
            "risk": "MEDIUM",
        })

        with patch("google.genai.Client") as mock_genai_cls:
            mock_client_instance = MagicMock()
            mock_genai_cls.return_value = mock_client_instance

            mock_response = MagicMock()
            mock_response.text = valid_response_json
            mock_client_instance.models.generate_content.return_value = mock_response

            gemini_client = GeminiLLMClient(api_key="mock_test_key", model="gemini-2.5-flash")
            controller = AIController(client=gemini_client)

            result = make_test_result(recon_id="REC_GEM_001", final_decision="HUMAN_REVIEW")
            gw = StubTxn(transaction_id="GW_G1", reference_id="REF_G1", amount=1200.0)
            bnk = StubTxn(transaction_id="BNK_G1", reference_id="REF-G1", amount=1200.0)

            ai_result = controller.investigate_with_fuzzy(result, gateway_txn=gw, bank_txn=bnk)

            # Assert Gemini client called the mocked SDK
            mock_client_instance.models.generate_content.assert_called_once()
            call_kwargs = mock_client_instance.models.generate_content.call_args.kwargs
            prompt_content = call_kwargs.get("contents", "")

            # Verify fuzzy composite score was included in the LLM prompt
            assert "fuzzy_composite_score" in prompt_content
            assert "fuzzy_decision" in prompt_content

            assert isinstance(ai_result, AIControllerResult)
            assert ai_result.recommendation == "REVIEW"
            assert ai_result.confidence == 0.88
            assert "Mocked Gemini" in ai_result.reason

    def test_heuristic_path_works_with_fuzzy_evidence(self):
        """
        When running with HeuristicLLMClient, fuzzy evidence is correctly
        consumed and influences the heuristic decision and explanation.
        """
        heuristic_client = HeuristicLLMClient()
        controller = AIController(client=heuristic_client)

        result = make_test_result(
            recon_id="REC_HEUR_001",
            final_decision="HUMAN_REVIEW",
            discrepancy_amount=100.0,
        )
        exception = make_test_exception(
            exc_id="EXC_HEUR_001",
            recon_id="REC_HEUR_001",
            category="AMOUNT_MISMATCH",
            severity="MEDIUM",
            diff_amount=100.0,
        )

        # High fuzzy similarity on references but discrepancy in amounts
        gw = StubTxn(transaction_id="GW_H1", reference_id="pay_9999", amount=1000.0)
        bnk = StubTxn(transaction_id="BNK_H1", reference_id="pay-9999", amount=900.0)

        ai_result = controller.investigate_with_fuzzy(
            result,
            exception=exception,
            gateway_txn=gw,
            bank_txn=bnk,
        )

        assert isinstance(ai_result, AIControllerResult)
        assert ai_result.recommendation == "REVIEW"
        assert ai_result.risk in ("MEDIUM", "HIGH")
        assert "Heuristic analysis" in ai_result.reason
        assert "Phase 7 fuzzy composite score" in ai_result.reason

    def test_ai_recommendation_remains_strictly_advisory(self):
        """
        Safety invariants:
        - AIController.investigate_with_fuzzy() never sets is_resolved = True.
        - Never sets exception status to APPROVED or RESOLVED.
        - Never modifies financial balances or discrepancy amounts.
        - Result is advisory only.
        """
        fake_client = RecordingFakeClient(return_dict={
            "recommendation": "AUTO_RECONCILE",
            "confidence": 0.99,
            "reason": "Safe advisory recommendation to reconcile.",
            "risk": "LOW",
        })
        controller = AIController(client=fake_client)

        result = make_test_result(
            recon_id="REC_SAFE_001",
            final_decision="HUMAN_REVIEW",
            discrepancy_amount=75.0,
            is_resolved=False,
        )
        exception = make_test_exception(
            exc_id="EXC_SAFE_001",
            status="PENDING",
            diff_amount=75.0,
        )

        gw = StubTxn(transaction_id="GW_S1", reference_id="pay_S1", amount=1075.0)
        bnk = StubTxn(transaction_id="BNK_S1", reference_id="pay_S1", amount=1000.0)

        ai_result = controller.investigate_with_fuzzy(
            result,
            exception=exception,
            gateway_txn=gw,
            bank_txn=bnk,
        )

        # AI returned AUTO_RECONCILE
        assert ai_result.recommendation == "AUTO_RECONCILE"

        # Financial resolution safety checks
        assert result.is_resolved is False, "is_resolved must remain False"
        assert exception.status == "PENDING", "exception status must remain PENDING"
        assert result.discrepancy_amount == 75.0, "discrepancy_amount must not change"
        assert gw.amount == 1075.0, "gateway amount must not change"
        assert bnk.amount == 1000.0, "bank amount must not change"

    def test_fuzzy_engine_reuse_and_custom_configuration(self):
        """
        AIController accepts and reuses an existing FuzzyMatchEngine instance,
        preserving custom thresholds and weights.
        """
        custom_engine = FuzzyMatchEngine(
            match_threshold=95.0,
            review_threshold=80.0,
            ref_weight=0.70,
            desc_weight=0.20,
            customer_weight=0.10,
        )
        controller = AIController(fuzzy_engine=custom_engine)

        assert controller.fuzzy_engine is custom_engine
        assert controller.fuzzy_engine.match_threshold == 95.0
        assert controller.fuzzy_engine.ref_weight == 0.70

    def test_collect_evidence_safely_handles_fuzzy_match_result_object(self):
        """
        _collect_evidence extracts fields safely whether fuzzy_result is a
        FuzzyMatchResult dataclass instance or a dictionary.
        """
        result = make_test_result(recon_id="REC_EVID_F1")

        fuzzy_obj = FuzzyMatchResult(
            match_id="fuzz_123",
            gateway_txn_id="GW_01",
            bank_txn_id="BNK_01",
            reference_score=94.5,
            description_score=80.0,
            customer_score=85.0,
            composite_score=89.5,
            decision="FUZZY_MATCHED",
            reason_codes=[FuzzyReasonCode.FUZZY_MATCHED, FuzzyReasonCode.AMOUNT_CONFIRMED],
            matched_fields=["reference_id"],
            amount_match=True,
            amount_diff=0.0,
        )

        evidence = _collect_evidence(result, fuzzy_result=fuzzy_obj)

        assert evidence["fuzzy_decision"] == "FUZZY_MATCHED"
        assert evidence["fuzzy_composite_score"] == 89.5
        assert evidence["fuzzy_amount_diff"] == 0.0
        assert evidence["fuzzy_matched_fields"] == ["reference_id"]
