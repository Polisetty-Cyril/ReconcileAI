"""
ReconcileAI - Phase 8 AI Controller & LLM Reasoning Test Suite

Comprehensive test suite covering:
  A. AIControllerResult schema (validation, normalization, safety overrides, to_db_dict)
  B. LLM Client behavior (BaseLLMClient, Heuristic reasoning, rules, fallbacks, JSON parsing)
  C. Provider Factory (get_llm_client, fallback behaviors, empty keys)
  D. Evidence Collection (_collect_evidence across Phase 6, Phase 7, and exceptions)
  E. AIController.investigate() (injected clients, fallbacks, Phase 6 auto-reconcile preservation)
  F. Safety Boundaries (advisory recommendations, no auto-approval, is_resolved=False)
  G. Persistence (investigate_and_persist, DB columns, AuditLog entries)
  H. Batch Processing (process_reconciliation_summary with multi-source correlation)
"""

from __future__ import annotations

import pytest
from typing import Any, Dict, Optional
from unittest.mock import MagicMock
from pydantic import ValidationError

from backend.database import SessionLocal, init_db
from backend.models.audit import AuditLog
from backend.models.exception import ReconciliationException
from backend.models.reconciliation import ReconciliationResult
from backend.schemas.ai_controller import (
    AIControllerResult,
    ALLOWED_RECOMMENDATIONS,
    ALLOWED_RISKS,
)
from backend.services.ai_controller import (
    AIController,
    _collect_evidence,
    _validate_and_build,
)
from backend.services.llm_client import (
    BaseLLMClient,
    HeuristicLLMClient,
    OpenAILLMClient,
    GroqLLMClient,
    GeminiLLMClient,
    get_llm_client,
    _build_prompt,
    _parse_llm_json,
)


# ===========================================================================
# Fixtures & Test Helpers
# ===========================================================================

@pytest.fixture(scope="module", autouse=True)
def setup_test_database():
    """Initializes the database schema and clears prior test data."""
    init_db()
    db = SessionLocal()
    try:
        db.query(AuditLog).filter(
            (AuditLog.audit_id.like("%TEST_AI_%")) | (AuditLog.entity_id.like("%TEST_AI_%"))
        ).delete(synchronize_session=False)
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like("%TEST_AI_%")).delete(synchronize_session=False)
        db.query(ReconciliationResult).filter(ReconciliationResult.reconciliation_id.like("%TEST_AI_%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield
    # Cleanup after test suite
    db = SessionLocal()
    try:
        db.query(AuditLog).filter(
            (AuditLog.audit_id.like("%TEST_AI_%")) | (AuditLog.entity_id.like("%TEST_AI_%"))
        ).delete(synchronize_session=False)
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like("%TEST_AI_%")).delete(synchronize_session=False)
        db.query(ReconciliationResult).filter(ReconciliationResult.reconciliation_id.like("%TEST_AI_%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


@pytest.fixture
def db_session():
    """Provides a transactional database session for tests."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


class FakeLLMClient(BaseLLMClient):
    """Custom fake client for testing AIController injection."""

    def __init__(self, response_dict: Optional[Dict[str, Any]] = None, should_raise: bool = False):
        self.response_dict = response_dict or {
            "recommendation": "REVIEW",
            "confidence": 0.88,
            "reason": "Fake LLM reasoning statement.",
            "risk": "MEDIUM",
        }
        self.should_raise = should_raise
        self.call_count = 0
        self.last_evidence: Optional[Dict[str, Any]] = None

    def reason(self, evidence: Dict[str, Any]) -> Dict[str, Any]:
        self.call_count += 1
        self.last_evidence = evidence
        if self.should_raise:
            raise RuntimeError("Injected LLM network / API failure")
        return self.response_dict


def make_test_result(
    recon_id: str = "TEST_AI_REC_001",
    gw_id: str = "TEST_AI_GW_001",
    bank_id: str = "TEST_AI_BNK_001",
    erp_id: str = "TEST_AI_ERP_001",
    match_score: float = 85.0,
    matching_method: str = "EXACT_RULE",
    final_decision: str = "HUMAN_REVIEW",
    discrepancy_amount: float = 150.0,
    is_resolved: bool = False,
) -> ReconciliationResult:
    """Helper to create ReconciliationResult model instances."""
    return ReconciliationResult(
        reconciliation_id=recon_id,
        gateway_transaction_id=gw_id,
        bank_transaction_id=bank_id,
        erp_invoice_id=erp_id,
        match_score=match_score,
        matching_method=matching_method,
        final_decision=final_decision,
        discrepancy_amount=discrepancy_amount,
        is_resolved=is_resolved,
    )


def make_test_exception(
    exc_id: str = "TEST_AI_EXC_001",
    recon_id: str = "TEST_AI_REC_001",
    txn_id: str = "TEST_AI_GW_001",
    category: str = "AMOUNT_MISMATCH",
    severity: str = "HIGH",
    diff_amount: float = 150.0,
    status: str = "OPEN",
) -> ReconciliationException:
    """Helper to create ReconciliationException model instances."""
    return ReconciliationException(
        exception_id=exc_id,
        reconciliation_id=recon_id,
        transaction_id=txn_id,
        category=category,
        severity=severity,
        difference_amount=diff_amount,
        status=status,
    )


# ===========================================================================
# A. AIControllerResult Schema Tests
# ===========================================================================

class TestAIControllerResultSchema:
    """Tests for backend/schemas/ai_controller.py."""

    def test_valid_recommendations_and_risks(self):
        """All allowed recommendations and risks must pass validation."""
        for rec in ALLOWED_RECOMMENDATIONS:
            for risk in ALLOWED_RISKS:
                result = AIControllerResult(
                    recommendation=rec,
                    confidence=0.85,
                    reason=f"Valid test for {rec} and {risk}",
                    risk=risk,
                )
                assert result.recommendation == rec
                assert result.risk == risk
                assert result.confidence == 0.85

    def test_normalization_lowercase_and_whitespace(self):
        """Recommendation and risk should normalize whitespace and lowercase."""
        result = AIControllerResult(
            recommendation="  review  ",
            confidence=0.75,
            reason="Whitespace test",
            risk=" medium ",
        )
        assert result.recommendation == "REVIEW"
        assert result.risk == "MEDIUM"

        result_esc = AIControllerResult(
            recommendation="escalate",
            confidence=0.90,
            reason="Lowercase test",
            risk="critical",
        )
        assert result_esc.recommendation == "ESCALATE"
        assert result_esc.risk == "CRITICAL"

    def test_invalid_recommendation_raises_validation_error(self):
        """Invalid recommendation strings must raise ValidationError."""
        for invalid in ["APPROVE", "RESOLVE", "REFUND", "INVALID_REC", ""]:
            with pytest.raises(ValidationError):
                AIControllerResult(
                    recommendation=invalid,
                    confidence=0.80,
                    reason="Invalid rec test",
                    risk="LOW",
                )

    def test_invalid_risk_raises_validation_error(self):
        """Invalid risk levels must raise ValidationError."""
        for invalid in ["NONE", "VERY_HIGH", "SEVERE", "UNKNOWN", ""]:
            with pytest.raises(ValidationError):
                AIControllerResult(
                    recommendation="REVIEW",
                    confidence=0.80,
                    reason="Invalid risk test",
                    risk=invalid,
                )

    def test_confidence_rounding_and_range(self):
        """Confidence should be rounded to 4 decimals and validated in [0.0, 1.0]."""
        result = AIControllerResult(
            recommendation="REVIEW",
            confidence=0.856789,
            reason="Rounding test",
            risk="LOW",
        )
        assert result.confidence == 0.8568

        # Out of bounds float values should raise ValidationError
        with pytest.raises(ValidationError):
            AIControllerResult(
                recommendation="REVIEW",
                confidence=1.5,
                reason="Above 1.0",
                risk="LOW",
            )
        with pytest.raises(ValidationError):
            AIControllerResult(
                recommendation="REVIEW",
                confidence=-0.1,
                reason="Below 0.0",
                risk="LOW",
            )

    def test_auto_reconcile_low_confidence_safety_override(self):
        """AUTO_RECONCILE with confidence < 0.65 must be overridden to REVIEW."""
        # Low confidence (< 0.65) -> downgraded to REVIEW
        result = AIControllerResult(
            recommendation="AUTO_RECONCILE",
            confidence=0.60,
            reason="Heuristic suggests match.",
            risk="LOW",
        )
        assert result.recommendation == "REVIEW"
        assert "[Safety override]" in result.reason
        assert "below the 0.65 threshold" in result.reason

        # High confidence (>= 0.65) -> stays AUTO_RECONCILE
        result_ok = AIControllerResult(
            recommendation="AUTO_RECONCILE",
            confidence=0.95,
            reason="Deterministic exact match.",
            risk="LOW",
        )
        assert result_ok.recommendation == "AUTO_RECONCILE"
        assert result_ok.confidence == 0.95

    def test_to_db_dict(self):
        """to_db_dict() must format values suitable for database columns."""
        result = AIControllerResult(
            recommendation="REVIEW",
            confidence=0.875,
            reason="Discrepancy requires reviewer inspection.",
            risk="MEDIUM",
        )
        db_dict = result.to_db_dict()
        assert db_dict == {
            "ai_recommendation": "REVIEW",
            "ai_confidence": 87.5,  # 0.875 * 100
            "ai_reasoning": "Discrepancy requires reviewer inspection.",
        }


# ===========================================================================
# B. LLM Client Behavior Tests
# ===========================================================================

class TestLLMClientBehavior:
    """Tests for BaseLLMClient and HeuristicLLMClient in backend/services/llm_client.py."""

    def test_base_client_validate_raw_success(self):
        """_validate_raw returns True for fully conforming raw response dicts."""
        client = HeuristicLLMClient()
        valid_raw = {
            "recommendation": "REVIEW",
            "confidence": 0.85,
            "reason": "Valid reason.",
            "risk": "MEDIUM",
        }
        assert client._validate_raw(valid_raw) is True

    def test_base_client_validate_raw_failures(self):
        """_validate_raw returns False on missing keys, bad enums, or bad confidence."""
        client = HeuristicLLMClient()

        # Missing required key
        assert client._validate_raw({"recommendation": "REVIEW", "confidence": 0.8, "risk": "LOW"}) is False
        assert client._validate_raw({"confidence": 0.8, "reason": "r", "risk": "LOW"}) is False

        # Bad confidence
        assert client._validate_raw({"recommendation": "REVIEW", "confidence": 1.5, "reason": "r", "risk": "LOW"}) is False
        assert client._validate_raw({"recommendation": "REVIEW", "confidence": -0.2, "reason": "r", "risk": "LOW"}) is False
        assert client._validate_raw({"recommendation": "REVIEW", "confidence": "non-numeric", "reason": "r", "risk": "LOW"}) is False

        # Bad recommendation or risk
        assert client._validate_raw({"recommendation": "APPROVE", "confidence": 0.8, "reason": "r", "risk": "LOW"}) is False
        assert client._validate_raw({"recommendation": "REVIEW", "confidence": 0.8, "reason": "r", "risk": "INVALID"}) is False

    def test_heuristic_client_rule_lookup_table(self):
        """Heuristic client should map known category/severity pairs correctly."""
        heuristic = HeuristicLLMClient()

        # AMOUNT_MISMATCH CRITICAL -> ESCALATE
        res = heuristic.reason({"category": "AMOUNT_MISMATCH", "severity": "CRITICAL", "gateway_txn_id": "GW1"})
        assert res["recommendation"] == "ESCALATE"
        assert res["risk"] == "CRITICAL"
        assert res["confidence"] >= 0.92

        # AMOUNT_MISMATCH HIGH -> REVIEW
        res2 = heuristic.reason({"category": "AMOUNT_MISMATCH", "severity": "HIGH", "gateway_txn_id": "GW1"})
        assert res2["recommendation"] == "REVIEW"
        assert res2["risk"] == "HIGH"
        assert res2["confidence"] == 0.90

        # FAILED_PAYMENT LOW -> AUTO_RECONCILE
        res3 = heuristic.reason({"category": "FAILED_PAYMENT", "severity": "LOW", "gateway_txn_id": "GW1"})
        assert res3["recommendation"] == "AUTO_RECONCILE"
        assert res3["risk"] == "LOW"
        assert res3["confidence"] == 0.98

        # DUPLICATE_TRANSACTION CRITICAL -> ESCALATE
        res4 = heuristic.reason({"category": "DUPLICATE_TRANSACTION", "severity": "CRITICAL", "gateway_txn_id": "GW1"})
        assert res4["recommendation"] == "ESCALATE"
        assert res4["risk"] == "CRITICAL"
        assert res4["confidence"] >= 0.95

        # Unknown category/severity fallback
        res_unk = heuristic.reason({"category": "UNEXPECTED_NEW_CAT", "severity": "LOW", "gateway_txn_id": "GW1"})
        assert res_unk["recommendation"] == "REVIEW"
        assert res_unk["risk"] == "MEDIUM"
        assert res_unk["confidence"] == 0.70

    def test_heuristic_client_insufficient_evidence_guard(self):
        """Empty gateway/bank IDs and UNKNOWN category should flag insufficient evidence."""
        heuristic = HeuristicLLMClient()
        res = heuristic.reason({
            "gateway_txn_id": None,
            "bank_txn_id": None,
            "category": "UNKNOWN",
        })
        assert res["recommendation"] == "REVIEW"
        assert res["confidence"] == 0.60
        assert res["risk"] == "MEDIUM"
        assert "Insufficient evidence" in res["reason"]

    def test_heuristic_client_large_amount_escalation_override(self):
        """Discrepancies > 10,000 INR must automatically escalate with CRITICAL risk."""
        heuristic = HeuristicLLMClient()
        # Even with LOW severity category, high amount difference must force ESCALATE
        evidence = {
            "category": "AMOUNT_MISMATCH",
            "severity": "LOW",
            "difference_amount": 25000.0,
            "gateway_txn_id": "GW_LARGE",
        }
        res = heuristic.reason(evidence)
        assert res["recommendation"] == "ESCALATE"
        assert res["risk"] == "CRITICAL"
        assert res["confidence"] >= 0.92
        assert "25,000.00" in res["reason"]

    def test_heuristic_client_conflicting_evidence_penalty(self):
        """Conflicting signals between Phase 6 and Phase 7 must cap confidence and elevate risk."""
        heuristic = HeuristicLLMClient()
        evidence = {
            "category": "AMOUNT_MISMATCH",
            "severity": "MEDIUM",
            "difference_amount": 100.0,
            "gateway_txn_id": "GW_CONFLICT",
            "phase6_decision": "HUMAN_REVIEW",
            "fuzzy_decision": "FUZZY_MATCHED",
            "fuzzy_composite_score": 88.5,
        }
        res = heuristic.reason(evidence)
        # Conflicting evidence caps confidence at 0.70 and elevates risk from MEDIUM to HIGH
        assert res["confidence"] <= 0.70
        assert res["risk"] in ("HIGH", "CRITICAL")
        assert "Conflicting evidence detected" in res["reason"]

    def test_heuristic_client_deterministic_output(self):
        """Heuristic client must produce identical output across repeated runs."""
        heuristic = HeuristicLLMClient()
        evidence = {
            "category": "DATE_MISMATCH",
            "severity": "HIGH",
            "difference_amount": 0.0,
            "gateway_txn_id": "GW_DET_1",
            "bank_txn_id": "BNK_DET_1",
        }
        run1 = heuristic.reason(evidence)
        run2 = heuristic.reason(evidence)
        run3 = heuristic.reason(evidence)
        assert run1 == run2 == run3

    def test_json_parsing_and_markdown_fences(self):
        """_parse_llm_json handles raw JSON, markdown-wrapped JSON, and errors on malformed input."""
        raw_json = '{"recommendation": "REVIEW", "confidence": 0.8, "reason": "ok", "risk": "LOW"}'
        assert _parse_llm_json(raw_json) == {
            "recommendation": "REVIEW", "confidence": 0.8, "reason": "ok", "risk": "LOW"
        }

        # Markdown fenced JSON
        fenced_json = '```json\n{"recommendation": "ESCALATE", "confidence": 0.95, "reason": "fenced", "risk": "CRITICAL"}\n```'
        assert _parse_llm_json(fenced_json) == {
            "recommendation": "ESCALATE", "confidence": 0.95, "reason": "fenced", "risk": "CRITICAL"
        }

        # Invalid JSON
        with pytest.raises(ValueError):
            _parse_llm_json("This is plain text without JSON formatting.")

    def test_prompt_builder(self):
        """_build_prompt must serialize non-empty evidence keys into prompt text."""
        evidence = {
            "reconciliation_id": "REC_P_1",
            "gateway_txn_id": "GW_P_1",
            "category": "AMOUNT_MISMATCH",
            "empty_val": None,
            "blank_val": "",
        }
        prompt = _build_prompt(evidence)
        assert "reconciliation_id: REC_P_1" in prompt
        assert "gateway_txn_id: GW_P_1" in prompt
        assert "category: AMOUNT_MISMATCH" in prompt
        assert "empty_val" not in prompt
        assert "blank_val" not in prompt

    def test_real_provider_stubs_raise_runtime_error_if_sdk_missing(self, monkeypatch):
        """Instantiating real providers when SDK is not installed raises RuntimeError."""
        # Ensure import failure simulation
        import sys
        monkeypatch.setitem(sys.modules, "openai", None)
        with pytest.raises(RuntimeError):
            OpenAILLMClient(api_key="test_key")

        monkeypatch.setitem(sys.modules, "groq", None)
        with pytest.raises(RuntimeError):
            GroqLLMClient(api_key="test_key")

        monkeypatch.setitem(sys.modules, "google.generativeai", None)
        with pytest.raises(RuntimeError):
            GeminiLLMClient(api_key="test_key")


# ===========================================================================
# C. Provider Factory Tests
# ===========================================================================

class TestProviderFactory:
    """Tests for get_llm_client in backend/services/llm_client.py."""

    def test_get_llm_client_heuristic_default(self):
        """Explicit heuristic provider returns HeuristicLLMClient."""
        client = get_llm_client("heuristic", "")
        assert isinstance(client, HeuristicLLMClient)

        client2 = get_llm_client("HEURISTIC", "any_key")
        assert isinstance(client2, HeuristicLLMClient)

    def test_get_llm_client_empty_api_key_falls_back(self, monkeypatch):
        """Empty or None API keys always fall back to HeuristicLLMClient."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        assert isinstance(get_llm_client("openai", ""), HeuristicLLMClient)
        assert isinstance(get_llm_client("openai", None), HeuristicLLMClient)
        assert isinstance(get_llm_client("groq", "   "), HeuristicLLMClient)
        assert isinstance(get_llm_client("gemini", ""), HeuristicLLMClient)

    def test_get_llm_client_unsupported_provider(self):
        """Unsupported providers gracefully fall back to HeuristicLLMClient."""
        client = get_llm_client("claude_unsupported", "test_key")
        assert isinstance(client, HeuristicLLMClient)

        client_none = get_llm_client(None, "test_key")
        assert isinstance(client_none, HeuristicLLMClient)

    def test_get_llm_client_missing_sdk_fallback(self, monkeypatch):
        """If real provider constructor fails (e.g. missing SDK), factory falls back."""
        import sys
        monkeypatch.setitem(sys.modules, "openai", None)
        client = get_llm_client("openai", "valid_looking_key")
        assert isinstance(client, HeuristicLLMClient)


# ===========================================================================
# D. Evidence Collection Tests
# ===========================================================================

class TestEvidenceCollection:
    """Tests for _collect_evidence in backend/services/ai_controller.py."""

    def test_collect_evidence_phase6_signals(self):
        """_collect_evidence extracts all signals from ReconciliationResult."""
        result = make_test_result(
            recon_id="REC_EVID_1",
            gw_id="GW_EVID_1",
            bank_id="BNK_EVID_1",
            erp_id="ERP_EVID_1",
            match_score=90.0,
            matching_method="EXACT_RULE",
            final_decision="HUMAN_REVIEW",
            discrepancy_amount=250.0,
        )
        evidence = _collect_evidence(result)
        assert evidence["reconciliation_id"] == "REC_EVID_1"
        assert evidence["gateway_txn_id"] == "GW_EVID_1"
        assert evidence["bank_txn_id"] == "BNK_EVID_1"
        assert evidence["erp_invoice_id"] == "ERP_EVID_1"
        assert evidence["match_score"] == 90.0
        assert evidence["matching_method"] == "EXACT_RULE"
        assert evidence["phase6_decision"] == "HUMAN_REVIEW"
        assert evidence["discrepancy_amount"] == 250.0
        # Defaults when no exception
        assert evidence["category"] == "UNKNOWN"
        assert evidence["severity"] == "MEDIUM"
        assert evidence["difference_amount"] == 250.0

    def test_collect_evidence_with_exception(self):
        """_collect_evidence overrides category, severity, and difference_amount from exception."""
        result = make_test_result(recon_id="REC_EVID_2", discrepancy_amount=50.0)
        exception = make_test_exception(
            exc_id="EXC_EVID_2",
            recon_id="REC_EVID_2",
            category="AMOUNT_MISMATCH",
            severity="CRITICAL",
            diff_amount=50.0,
        )
        evidence = _collect_evidence(result, exception=exception)
        assert evidence["exception_id"] == "EXC_EVID_2"
        assert evidence["category"] == "AMOUNT_MISMATCH"
        assert evidence["severity"] == "CRITICAL"
        assert evidence["difference_amount"] == 50.0

    def test_collect_evidence_with_fuzzy_results(self):
        """_collect_evidence includes Phase 7 fuzzy matching metadata if present."""
        result = make_test_result(recon_id="REC_EVID_3")
        fuzzy_data = {
            "decision": "FUZZY_MATCHED",
            "composite_score": 89.2,
            "amount_diff": 0.0,
            "matched_fields": ["reference_id", "customer_name"],
        }
        evidence = _collect_evidence(result, fuzzy_result=fuzzy_data)
        assert evidence["fuzzy_decision"] == "FUZZY_MATCHED"
        assert evidence["fuzzy_composite_score"] == 89.2
        assert evidence["fuzzy_amount_diff"] == 0.0
        assert evidence["fuzzy_matched_fields"] == ["reference_id", "customer_name"]


# ===========================================================================
# E. AIController.investigate() Tests
# ===========================================================================

class TestAIControllerInvestigate:
    """Tests for AIController.investigate() method."""

    def test_investigate_with_injected_valid_client(self):
        """AIController uses injected custom client and returns validated AIControllerResult."""
        fake_client = FakeLLMClient(
            response_dict={
                "recommendation": "ESCALATE",
                "confidence": 0.94,
                "reason": "Custom injected agent detected high risk.",
                "risk": "HIGH",
            }
        )
        controller = AIController(client=fake_client)
        result = make_test_result(recon_id="REC_INV_1")

        ai_result = controller.investigate(result)
        assert isinstance(ai_result, AIControllerResult)
        assert ai_result.recommendation == "ESCALATE"
        assert ai_result.confidence == 0.94
        assert ai_result.risk == "HIGH"
        assert fake_client.call_count == 1

    def test_investigate_client_failure_falls_back_to_heuristic(self):
        """When injected primary client raises exception, controller falls back to heuristic engine."""
        fake_client = FakeLLMClient(should_raise=True)
        controller = AIController(client=fake_client)
        result = make_test_result(
            recon_id="REC_INV_2",
            discrepancy_amount=20000.0,  # Large amount trigger
        )
        exception = make_test_exception(
            exc_id="EXC_INV_2",
            recon_id="REC_INV_2",
            category="AMOUNT_MISMATCH",
            severity="CRITICAL",
            diff_amount=20000.0,
        )

        ai_result = controller.investigate(result, exception=exception)
        assert isinstance(ai_result, AIControllerResult)
        # Should have fallen back to HeuristicLLMClient which escalates > 10,000 INR
        assert ai_result.recommendation == "ESCALATE"
        assert ai_result.risk == "CRITICAL"
        assert ai_result.confidence >= 0.92
        assert fake_client.call_count == 1

    def test_investigate_malformed_client_response_falls_back(self):
        """When primary client returns structurally invalid dict failing schema validation, fallback triggers."""
        # Client returns invalid recommendation enum which raises ValidationError
        fake_client = FakeLLMClient(
            response_dict={"recommendation": "INVALID_ACTION", "confidence": 0.99, "risk": "CRITICAL"}
        )
        controller = AIController(client=fake_client)
        result = make_test_result(recon_id="REC_INV_3")
        exception = make_test_exception(
            exc_id="EXC_INV_3",
            category="FAILED_PAYMENT",
            severity="LOW",
        )

        ai_result = controller.investigate(result, exception=exception)
        assert isinstance(ai_result, AIControllerResult)
        # Fallback heuristic engine should handle FAILED_PAYMENT LOW -> AUTO_RECONCILE
        assert ai_result.recommendation == "AUTO_RECONCILE"
        assert ai_result.risk == "LOW"

    def test_investigate_phase6_auto_reconciled_preservation(self):
        """If Phase 6 decided AUTO_RECONCILED, AIController immediately preserves it without calling LLM."""
        fake_client = FakeLLMClient()
        controller = AIController(client=fake_client)
        result = make_test_result(
            recon_id="REC_INV_AUTO",
            final_decision="AUTO_RECONCILED",
            match_score=100.0,
            discrepancy_amount=0.0,
        )

        ai_result = controller.investigate(result)
        assert ai_result.recommendation == "AUTO_RECONCILE"
        assert ai_result.confidence == 1.0
        assert ai_result.risk == "LOW"
        assert "Phase 6 deterministic engine confirmed AUTO_RECONCILED" in ai_result.reason
        # Primary client was never invoked
        assert fake_client.call_count == 0

    def test_investigate_does_not_modify_result_object(self):
        """investigate() is a pure inspection method and does not mutate result attributes or DB."""
        controller = AIController(client=HeuristicLLMClient())
        result = make_test_result(
            recon_id="REC_INV_PURE",
            matching_method="EXACT_RULE",
        )
        orig_ai_rec = result.ai_recommendation
        orig_ai_conf = result.ai_confidence

        _ = controller.investigate(result)
        assert result.ai_recommendation == orig_ai_rec
        assert result.ai_confidence == orig_ai_conf
        assert result.matching_method == "EXACT_RULE"


# ===========================================================================
# F. Safety Boundaries Tests
# ===========================================================================

class TestSafetyBoundaries:
    """Safety boundary verification across AI controller actions."""

    def test_never_sets_is_resolved_true(self, db_session):
        """Phase 8 AI Controller must NEVER set ReconciliationResult.is_resolved = True."""
        controller = AIController(client=HeuristicLLMClient())
        result = make_test_result(
            recon_id="TEST_AI_SAFETY_RES_1",
            is_resolved=False,
        )
        db_session.add(result)
        db_session.commit()

        ai_result = controller.investigate_and_persist(db_session, result)
        db_session.commit()

        reloaded = db_session.query(ReconciliationResult).filter_by(reconciliation_id="TEST_AI_SAFETY_RES_1").first()
        assert reloaded.is_resolved is False

    def test_never_sets_exception_status_approved_or_resolved(self, db_session):
        """Phase 8 AI Controller must NEVER change exception status to APPROVED or RESOLVED."""
        controller = AIController(client=HeuristicLLMClient())
        result = make_test_result(recon_id="TEST_AI_SAFETY_EXC_1")
        exception = make_test_exception(
            exc_id="TEST_AI_SAFETY_EXC_1",
            recon_id="TEST_AI_SAFETY_EXC_1",
            status="OPEN",
            category="FAILED_PAYMENT",
            severity="LOW",
        )
        db_session.add(result)
        db_session.add(exception)
        db_session.commit()

        # Even when recommendation is AUTO_RECONCILE, exception status MUST remain OPEN
        ai_result = controller.investigate_and_persist(db_session, result, exception=exception)
        db_session.commit()

        assert ai_result.recommendation == "AUTO_RECONCILE"
        reloaded_exc = db_session.query(ReconciliationException).filter_by(exception_id="TEST_AI_SAFETY_EXC_1").first()
        assert reloaded_exc.status == "OPEN"
        assert reloaded_exc.status not in ("APPROVED", "RESOLVED")

    def test_recommendations_are_purely_advisory(self, db_session):
        """final_decision on result remains unchanged by AIController."""
        controller = AIController(client=HeuristicLLMClient())
        result = make_test_result(
            recon_id="TEST_AI_ADVISORY_1",
            final_decision="HUMAN_REVIEW",
        )
        db_session.add(result)
        db_session.commit()

        controller.investigate_and_persist(db_session, result)
        db_session.commit()

        reloaded = db_session.query(ReconciliationResult).filter_by(reconciliation_id="TEST_AI_ADVISORY_1").first()
        # Original deterministic final_decision remains HUMAN_REVIEW
        assert reloaded.final_decision == "HUMAN_REVIEW"
        # Advisory fields contain AI insights
        assert reloaded.ai_recommendation is not None
        assert reloaded.ai_confidence is not None
        assert reloaded.ai_reasoning is not None


# ===========================================================================
# G. Persistence Tests (investigate_and_persist)
# ===========================================================================

class TestPersistence:
    """Tests for investigate_and_persist in backend/services/ai_controller.py."""

    def test_investigate_and_persist_writes_columns(self, db_session):
        """investigate_and_persist updates result columns, exception explanation, and adds AuditLog."""
        controller = AIController(client=HeuristicLLMClient())
        result = make_test_result(
            recon_id="TEST_AI_PERSIST_1",
            gw_id="TEST_AI_GW_P1",
        )
        exception = make_test_exception(
            exc_id="TEST_AI_EXC_P1",
            recon_id="TEST_AI_PERSIST_1",
            category="REFERENCE_MISMATCH",
            severity="HIGH",
        )
        db_session.add(result)
        db_session.add(exception)
        db_session.commit()

        ai_res = controller.investigate_and_persist(db_session, result, exception=exception)
        db_session.commit()

        # Check result
        res = db_session.query(ReconciliationResult).filter_by(reconciliation_id="TEST_AI_PERSIST_1").first()
        assert res.ai_recommendation == ai_res.recommendation
        assert res.ai_confidence == round(ai_res.confidence * 100, 2)
        assert res.ai_reasoning == ai_res.reason
        assert res.matching_method == "HEURISTIC_FALLBACK"

        # Check exception
        exc = db_session.query(ReconciliationException).filter_by(exception_id="TEST_AI_EXC_P1").first()
        assert exc.ai_explanation == ai_res.reason

        # Check AuditLog
        audit = db_session.query(AuditLog).filter_by(entity_id="TEST_AI_PERSIST_1").first()
        assert audit is not None
        assert audit.actor == "AI_CONTROLLER"
        assert audit.action == "AI_REASONED"
        assert audit.entity == "RECONCILIATION"
        assert audit.new_value == ai_res.recommendation

    def test_investigate_and_persist_custom_client_method_label(self, db_session):
        """When a non-heuristic client is used, matching_method is labelled 'AI_REASONING'."""
        fake_client = FakeLLMClient()
        controller = AIController(client=fake_client)
        result = make_test_result(recon_id="TEST_AI_PERSIST_2")
        db_session.add(result)
        db_session.commit()

        controller.investigate_and_persist(db_session, result)
        db_session.commit()

        res = db_session.query(ReconciliationResult).filter_by(reconciliation_id="TEST_AI_PERSIST_2").first()
        assert res.matching_method == "AI_REASONING"

    def test_persist_result_writes_supplied_result_and_skips_llm(self, db_session):
        """persist_result writes the supplied AIControllerResult directly to DB without calling the LLM."""
        fake_client = FakeLLMClient()
        controller = AIController(client=fake_client)
        result = make_test_result(
            recon_id="TEST_AI_PERSIST_PRE_1",
            gw_id="TEST_AI_GW_PRE_1",
            final_decision="HUMAN_REVIEW",
            discrepancy_amount=250.0,
            is_resolved=False,
        )
        exception = make_test_exception(
            exc_id="TEST_AI_EXC_PRE_1",
            recon_id="TEST_AI_PERSIST_PRE_1",
            category="AMOUNT_MISMATCH",
            severity="MEDIUM",
            diff_amount=250.0,
            status="OPEN",
        )
        db_session.add(result)
        db_session.add(exception)
        db_session.commit()

        precomputed_ai = AIControllerResult(
            recommendation="REVIEW",
            confidence=0.92,
            reason="Precomputed advisory reasoning for persistence test.",
            risk="LOW",
        )

        persisted_res = controller.persist_result(
            db_session,
            result,
            precomputed_ai,
            exception=exception,
        )
        db_session.commit()

        # 1. Zero LLM calls made
        assert fake_client.call_count == 0

        # 2. Correct advisory AI fields written
        assert persisted_res is precomputed_ai
        res = db_session.query(ReconciliationResult).filter_by(reconciliation_id="TEST_AI_PERSIST_PRE_1").first()
        assert res.ai_recommendation == "REVIEW"
        assert res.ai_confidence == 92.0
        assert res.ai_reasoning == "Precomputed advisory reasoning for persistence test."
        assert res.matching_method == "AI_REASONING"

        # 3. Exception explanation updated
        exc = db_session.query(ReconciliationException).filter_by(exception_id="TEST_AI_EXC_PRE_1").first()
        assert exc.ai_explanation == "Precomputed advisory reasoning for persistence test."

        # 4. AuditLog created
        audit = db_session.query(AuditLog).filter_by(entity_id="TEST_AI_PERSIST_PRE_1").first()
        assert audit is not None
        assert audit.actor == "AI_CONTROLLER"
        assert audit.action == "AI_REASONED"
        assert audit.new_value == "REVIEW"

        # 5. Safety invariants: Exception remains OPEN, is_resolved remains False, financial values unchanged
        assert exc.status == "OPEN"
        assert res.is_resolved is False
        assert res.final_decision == "HUMAN_REVIEW"
        assert res.discrepancy_amount == 250.0
        assert exc.difference_amount == 250.0

    def test_persist_result_with_heuristic_client_method_label(self, db_session):
        """persist_result assigns 'HEURISTIC_FALLBACK' when HeuristicLLMClient is used."""
        controller = AIController(client=HeuristicLLMClient())
        result = make_test_result(recon_id="TEST_AI_PERSIST_HEUR_1")
        db_session.add(result)
        db_session.commit()

        precomputed = AIControllerResult(
            recommendation="AUTO_RECONCILE",
            confidence=0.95,
            reason="Heuristic precomputed reason.",
            risk="LOW",
        )
        controller.persist_result(db_session, result, precomputed)
        db_session.commit()

        res = db_session.query(ReconciliationResult).filter_by(reconciliation_id="TEST_AI_PERSIST_HEUR_1").first()
        assert res.matching_method == "HEURISTIC_FALLBACK"
        assert res.ai_recommendation == "AUTO_RECONCILE"

    def test_persist_result_chained_with_investigate_with_fuzzy(self, db_session):
        """investigate_with_fuzzy followed by persist_result causes exactly ONE LLM call."""
        fake_client = FakeLLMClient(response_dict={
            "recommendation": "AUTO_RECONCILE",
            "confidence": 0.89,
            "reason": "Fuzzy and AI matched cleanly.",
            "risk": "LOW",
        })
        controller = AIController(client=fake_client)

        result = make_test_result(
            recon_id="TEST_AI_FUZZ_PERSIST_1",
            final_decision="HUMAN_REVIEW",
            discrepancy_amount=0.0,
            is_resolved=False,
        )
        exception = make_test_exception(
            exc_id="TEST_AI_FUZZ_EXC_1",
            recon_id="TEST_AI_FUZZ_PERSIST_1",
            status="OPEN",
        )
        db_session.add(result)
        db_session.add(exception)
        db_session.commit()

        # Phase 7 + Phase 8 investigation
        ai_result = controller.investigate_with_fuzzy(result, exception=exception)
        assert fake_client.call_count == 1

        # Persistence step: should NOT invoke LLM again
        controller.persist_result(db_session, result, ai_result, exception=exception)
        db_session.commit()
        assert fake_client.call_count == 1  # Still 1!

        reloaded = db_session.query(ReconciliationResult).filter_by(reconciliation_id="TEST_AI_FUZZ_PERSIST_1").first()
        assert reloaded.ai_recommendation == "AUTO_RECONCILE"
        assert reloaded.is_resolved is False  # Advisory only!


# ===========================================================================
# H. Batch Processing Tests (process_reconciliation_summary)
# ===========================================================================

class TestBatchProcessing:
    """Tests for process_reconciliation_summary in backend/services/ai_controller.py."""

    def test_process_reconciliation_summary_end_to_end(self, db_session):
        """process_reconciliation_summary correlates results, exceptions, and fuzzy matches."""
        controller = AIController(client=HeuristicLLMClient())

        # Build 3 results
        r1 = make_test_result(recon_id="TEST_AI_BATCH_1", gw_id="GW_B1", final_decision="AUTO_RECONCILED", match_score=100.0)
        r2 = make_test_result(recon_id="TEST_AI_BATCH_2", gw_id="GW_B2", final_decision="HUMAN_REVIEW", discrepancy_amount=300.0)
        r3 = make_test_result(recon_id="TEST_AI_BATCH_3", gw_id="GW_B3", final_decision="EXCEPTION", discrepancy_amount=15000.0)

        # Exceptions for r2 and r3
        e2 = make_test_exception(exc_id="TEST_AI_BEXC_2", recon_id="TEST_AI_BATCH_2", category="DATE_MISMATCH", severity="LOW")
        e3 = make_test_exception(exc_id="TEST_AI_BEXC_3", recon_id="TEST_AI_BATCH_3", category="AMOUNT_MISMATCH", severity="CRITICAL", diff_amount=15000.0)

        # Fuzzy match for r2
        fuzzy_results = [
            {
                "gateway_txn_id": "GW_B2",
                "decision": "FUZZY_MATCHED",
                "composite_score": 87.0,
                "amount_diff": 0.0,
            }
        ]

        summary = {
            "results": [r1, r2, r3],
            "exceptions": [e2, e3],
        }

        # Run batch processing
        ai_results = controller.process_reconciliation_summary(
            db=db_session,
            summary=summary,
            fuzzy_results=fuzzy_results,
        )

        assert len(ai_results) == 3

        # r1 was AUTO_RECONCILED from Phase 6 -> preserved
        assert ai_results[0].recommendation == "AUTO_RECONCILE"
        assert ai_results[0].confidence == 1.0

        # r2 was DATE_MISMATCH LOW -> REVIEW
        assert ai_results[1].recommendation == "REVIEW"

        # r3 was AMOUNT_MISMATCH CRITICAL (15k) -> ESCALATE
        assert ai_results[2].recommendation == "ESCALATE"
        assert ai_results[2].risk == "CRITICAL"

        # Verify DB persistence of audit logs and AI columns
        persisted_r3 = db_session.query(ReconciliationResult).filter_by(reconciliation_id="TEST_AI_BATCH_3").first()
        assert persisted_r3.ai_recommendation == "ESCALATE"

        persisted_e3 = db_session.query(ReconciliationException).filter_by(exception_id="TEST_AI_BEXC_3").first()
        assert persisted_e3.ai_explanation is not None

        audit_entries = db_session.query(AuditLog).filter(AuditLog.entity_id.like("TEST_AI_BATCH_%")).all()
        assert len(audit_entries) == 3
