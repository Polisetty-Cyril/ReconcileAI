"""
ReconcileAI - Gemini LLM Client Unit Test Suite

Verifies:
  1. Valid structured response generation and parsing with google-genai SDK
  2. Malformed / corrupted response fallback behavior
  3. Provider failure (API errors, network exceptions) fallback behavior
  4. Missing API key and environment configuration handling
  5. Schema validation (missing keys, invalid enums, confidence bounds, safety override)
  6. Secret leakage prevention (no keys in repr, logs, or error strings)
  7. Model configuration via environment variable (GEMINI_MODEL) and constructor
  8. Financial safety preservation (advisory only, no autonomous resolution)

ALL tests mock the Gemini SDK. ZERO real network calls are made.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
import pytest
from unittest.mock import MagicMock, patch

from backend.database import SessionLocal, init_db
from backend.models.audit import AuditLog
from backend.models.exception import ReconciliationException
from backend.models.reconciliation import ReconciliationResult
from backend.schemas.ai_controller import AIControllerResult
from backend.services.ai_controller import AIController
from backend.services.llm_client import (
    BaseLLMClient,
    GeminiLLMClient,
    HeuristicLLMClient,
    get_llm_client,
    _sanitize_key,
)


# ---------------------------------------------------------------------------
# Test Fixtures & Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_evidence() -> dict:
    """Standard sample discrepancy evidence for tests."""
    return {
        "reconciliation_id": "REC_GEMINI_001",
        "gateway_txn_id": "GW_GEMINI_001",
        "bank_txn_id": "BNK_GEMINI_001",
        "category": "AMOUNT_MISMATCH",
        "severity": "HIGH",
        "discrepancy_amount": 150.0,
        "difference_amount": 150.0,
        "match_score": 85.0,
    }


@pytest.fixture
def mock_genai_client():
    """Mocks the google-genai Client and its models.generate_content."""
    with patch("google.genai.Client") as mock_client_cls:
        client_instance = MagicMock()
        mock_client_cls.return_value = client_instance
        yield client_instance


# ---------------------------------------------------------------------------
# 1. Valid Structured Response Tests
# ---------------------------------------------------------------------------

class TestGeminiValidStructuredResponse:
    """Tests proper processing of valid structured outputs from Gemini."""

    def test_valid_structured_response(self, mock_genai_client, mock_evidence):
        """Gemini client sends prompt, requests structured JSON, and parses valid response."""
        valid_json = json.dumps({
            "recommendation": "ESCALATE",
            "confidence": 0.94,
            "reason": "Large discrepancy between gateway and bank records.",
            "risk": "HIGH",
        })

        mock_response = MagicMock()
        mock_response.text = valid_json
        mock_genai_client.models.generate_content.return_value = mock_response

        client = GeminiLLMClient(api_key="test_api_key_123", model="gemini-2.5-flash")
        result = client.reason(mock_evidence)

        assert isinstance(result, dict)
        assert result["recommendation"] == "ESCALATE"
        assert result["confidence"] == 0.94
        assert result["reason"] == "Large discrepancy between gateway and bank records."
        assert result["risk"] == "HIGH"

        # Verify SDK was invoked with proper config
        mock_genai_client.models.generate_content.assert_called_once()
        call_kwargs = mock_genai_client.models.generate_content.call_args.kwargs
        assert call_kwargs["model"] == "gemini-2.5-flash"
        config = call_kwargs["config"]
        assert config.response_mime_type == "application/json"
        assert config.response_schema == AIControllerResult
        assert config.temperature == 0.1

    def test_integration_with_ai_controller(self, mock_genai_client, mock_evidence):
        """AIController wraps Gemini client and produces validated AIControllerResult."""
        valid_json = json.dumps({
            "recommendation": "REVIEW",
            "confidence": 0.88,
            "reason": "Reference mismatch detected. Requires manual confirmation.",
            "risk": "MEDIUM",
        })
        mock_response = MagicMock()
        mock_response.text = valid_json
        mock_genai_client.models.generate_content.return_value = mock_response

        gemini_client = GeminiLLMClient(api_key="test_key")
        controller = AIController(client=gemini_client)

        recon_result = ReconciliationResult(
            reconciliation_id="REC_GEMINI_INT_1",
            gateway_transaction_id="GW_G1",
            bank_transaction_id="BNK_G1",
            final_decision="HUMAN_REVIEW",
            discrepancy_amount=50.0,
        )
        ai_res = controller.investigate(recon_result)

        assert isinstance(ai_res, AIControllerResult)
        assert ai_res.recommendation == "REVIEW"
        assert ai_res.confidence == 0.88
        assert ai_res.risk == "MEDIUM"


# ---------------------------------------------------------------------------
# 2. Malformed Response Handling Tests
# ---------------------------------------------------------------------------

class TestGeminiMalformedResponse:
    """Tests safe fallback when Gemini returns invalid or corrupted data."""

    def test_corrupted_json_falls_back_to_heuristic(self, mock_genai_client, mock_evidence):
        """Non-JSON text output must not raise an error; safely falls back to Heuristic client."""
        mock_response = MagicMock()
        mock_response.text = "I am an AI and here is my text response without any JSON."
        mock_genai_client.models.generate_content.return_value = mock_response

        client = GeminiLLMClient(api_key="test_api_key_123")
        result = client.reason(mock_evidence)

        # Should fall back safely to HeuristicLLMClient
        assert isinstance(result, dict)
        assert result["recommendation"] in ("AUTO_RECONCILE", "REVIEW", "ESCALATE", "EXCEPTION")
        assert 0.0 <= result["confidence"] <= 1.0
        assert result["risk"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
        assert "Heuristic analysis:" in result["reason"]

    def test_empty_response_text_falls_back_to_heuristic(self, mock_genai_client, mock_evidence):
        """Empty or None text attribute from SDK response safely falls back."""
        mock_response = MagicMock()
        mock_response.text = ""
        mock_genai_client.models.generate_content.return_value = mock_response

        client = GeminiLLMClient(api_key="test_api_key_123")
        result = client.reason(mock_evidence)

        assert isinstance(result, dict)
        assert result["recommendation"] in ("AUTO_RECONCILE", "REVIEW", "ESCALATE", "EXCEPTION")
        assert "Heuristic analysis:" in result["reason"]


# ---------------------------------------------------------------------------
# 3. Provider Failure Handling Tests
# ---------------------------------------------------------------------------

class TestGeminiProviderFailure:
    """Tests handling of SDK network errors, timeouts, and API exceptions."""

    def test_api_exception_falls_back_to_heuristic(self, mock_genai_client, mock_evidence):
        """Google API exceptions must be caught and gracefully fall back to heuristic."""
        mock_genai_client.models.generate_content.side_effect = RuntimeError(
            "503 Service Unavailable: Google API quota exceeded"
        )

        client = GeminiLLMClient(api_key="test_api_key_123")
        result = client.reason(mock_evidence)

        assert isinstance(result, dict)
        assert result["recommendation"] in ("AUTO_RECONCILE", "REVIEW", "ESCALATE", "EXCEPTION")
        assert "Heuristic analysis:" in result["reason"]

    def test_timeout_exception_falls_back_safely(self, mock_genai_client, mock_evidence):
        """Network timeouts fall back safely without propagating unhandled exceptions."""
        mock_genai_client.models.generate_content.side_effect = TimeoutError("Request timed out")

        client = GeminiLLMClient(api_key="test_api_key_123")
        result = client.reason(mock_evidence)

        assert isinstance(result, dict)
        assert result["recommendation"] in ("AUTO_RECONCILE", "REVIEW", "ESCALATE", "EXCEPTION")


# ---------------------------------------------------------------------------
# 4. Missing API Key / Configuration Tests
# ---------------------------------------------------------------------------

class TestGeminiMissingConfiguration:
    """Tests behavior when GEMINI_API_KEY is not configured or missing."""

    def test_direct_instantiation_without_key_raises_value_error(self, monkeypatch):
        """Direct instantiation without API key and without env vars raises ValueError."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)

        with pytest.raises(ValueError, match="GEMINI_API_KEY is not configured"):
            GeminiLLMClient(api_key="")

        with pytest.raises(ValueError, match="GEMINI_API_KEY is not configured"):
            GeminiLLMClient(api_key=None)

    def test_factory_with_missing_key_falls_back_to_heuristic(self, monkeypatch):
        """get_llm_client('gemini', '') returns HeuristicLLMClient when no env key exists."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)

        client = get_llm_client("gemini", api_key="")
        assert isinstance(client, HeuristicLLMClient)

    def test_factory_picks_up_gemini_api_key_from_env(self, monkeypatch, mock_genai_client):
        """get_llm_client('gemini') automatically uses GEMINI_API_KEY from environment."""
        monkeypatch.setenv("GEMINI_API_KEY", "env_secret_key_98765")
        client = get_llm_client("gemini", api_key="")
        assert isinstance(client, GeminiLLMClient)


# ---------------------------------------------------------------------------
# 5. Schema Validation & Safety Override Tests
# ---------------------------------------------------------------------------

class TestGeminiSchemaValidation:
    """Tests structural validation on parsed responses and safety overrides."""

    def test_missing_required_key_triggers_fallback(self, mock_genai_client, mock_evidence):
        """Response missing 'risk' or 'recommendation' fails structural check and falls back."""
        incomplete_json = json.dumps({
            "recommendation": "REVIEW",
            "confidence": 0.85,
            "reason": "Missing risk field.",
            # 'risk' is intentionally omitted
        })
        mock_response = MagicMock()
        mock_response.text = incomplete_json
        mock_genai_client.models.generate_content.return_value = mock_response

        client = GeminiLLMClient(api_key="test_key")
        result = client.reason(mock_evidence)

        # Structural check failed -> fallback to heuristic
        assert "Heuristic analysis:" in result["reason"]

    def test_invalid_recommendation_enum_triggers_fallback(self, mock_genai_client, mock_evidence):
        """Disallowed recommendation values (e.g. APPROVE_PAYMENT) fail validation and fall back."""
        invalid_enum_json = json.dumps({
            "recommendation": "APPROVE_AND_DISBURSE",
            "confidence": 0.99,
            "reason": "Autonomous payment action attempted.",
            "risk": "LOW",
        })
        mock_response = MagicMock()
        mock_response.text = invalid_enum_json
        mock_genai_client.models.generate_content.return_value = mock_response

        client = GeminiLLMClient(api_key="test_key")
        result = client.reason(mock_evidence)

        assert result["recommendation"] != "APPROVE_AND_DISBURSE"
        assert "Heuristic analysis:" in result["reason"]

    def test_invalid_risk_enum_triggers_fallback(self, mock_genai_client, mock_evidence):
        """Invalid risk level (e.g. SEVERE) fails validation and falls back."""
        invalid_risk_json = json.dumps({
            "recommendation": "REVIEW",
            "confidence": 0.85,
            "reason": "Bad risk enum.",
            "risk": "SEVERE_CRITICAL",
        })
        mock_response = MagicMock()
        mock_response.text = invalid_risk_json
        mock_genai_client.models.generate_content.return_value = mock_response

        client = GeminiLLMClient(api_key="test_key")
        result = client.reason(mock_evidence)

        assert "Heuristic analysis:" in result["reason"]

    def test_out_of_range_confidence_triggers_fallback(self, mock_genai_client, mock_evidence):
        """Confidence outside [0.0, 1.0] fails validation and falls back."""
        bad_conf_json = json.dumps({
            "recommendation": "REVIEW",
            "confidence": 1.75,
            "reason": "Confidence exceeds 1.0",
            "risk": "LOW",
        })
        mock_response = MagicMock()
        mock_response.text = bad_conf_json
        mock_genai_client.models.generate_content.return_value = mock_response

        client = GeminiLLMClient(api_key="test_key")
        result = client.reason(mock_evidence)

        assert "Heuristic analysis:" in result["reason"]

    def test_auto_reconcile_low_confidence_safety_override(self, mock_genai_client, mock_evidence):
        """AUTO_RECONCILE with confidence < 0.65 must be downgraded to REVIEW."""
        low_conf_json = json.dumps({
            "recommendation": "AUTO_RECONCILE",
            "confidence": 0.55,
            "reason": "Weak signal suggests match.",
            "risk": "LOW",
        })
        mock_response = MagicMock()
        mock_response.text = low_conf_json
        mock_genai_client.models.generate_content.return_value = mock_response

        client = GeminiLLMClient(api_key="test_key")
        result = client.reason(mock_evidence)

        assert result["recommendation"] == "REVIEW"
        assert "[Safety override]" in result["reason"]
        assert "below the 0.65 threshold" in result["reason"]


# ---------------------------------------------------------------------------
# 6. Secret Leakage Prevention Tests
# ---------------------------------------------------------------------------

class TestGeminiSecretLeakagePrevention:
    """Ensures API keys are NEVER logged or exposed."""

    def test_api_key_not_in_repr_or_str(self, mock_genai_client):
        """Object representations must never contain the API key."""
        secret = "AIzaSyD-SUPER-SECRET-GEMINI-KEY-999"
        client = GeminiLLMClient(api_key=secret)

        assert secret not in repr(client)
        assert secret not in str(client)

    def test_api_key_not_in_logs_on_exception(self, mock_genai_client, mock_evidence, caplog):
        """If an API exception contains the API key in a URL or message, it is redacted."""
        secret = "AIzaSyD_LEAKY_SECRET_KEY_123456789"
        mock_genai_client.models.generate_content.side_effect = RuntimeError(
            f"Failed calling https://generativelanguage.googleapis.com/v1beta/models?key={secret}"
        )

        client = GeminiLLMClient(api_key=secret)
        with caplog.at_level(logging.WARNING):
            _ = client.reason(mock_evidence)

        log_output = caplog.text
        assert secret not in log_output
        assert "[REDACTED]" in log_output

    def test_sanitize_key_helper(self):
        """_sanitize_key utility successfully redacts known patterns and explicit secrets."""
        secret = "AIzaSyFakeKey12345678901234567890123"
        raw_err = f"API error with key={secret} occurred"
        sanitized = _sanitize_key(raw_err, secret=secret)
        assert secret not in sanitized
        assert "[REDACTED]" in sanitized


# ---------------------------------------------------------------------------
# 7. Model Configuration Tests
# ---------------------------------------------------------------------------

class TestGeminiModelConfiguration:
    """Verifies model selection via constructor, environment variable, and defaults."""

    def test_default_model(self, mock_genai_client, monkeypatch):
        """Defaults to gemini-2.5-flash when no model is specified."""
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        client = GeminiLLMClient(api_key="test_key")
        assert client._model == "gemini-2.5-flash"

    def test_model_from_constructor_parameter(self, mock_genai_client):
        """Constructor model parameter takes precedence."""
        client = GeminiLLMClient(api_key="test_key", model="gemini-3.7-flash")
        assert client._model == "gemini-3.7-flash"

    def test_model_from_environment_variable(self, mock_genai_client, monkeypatch):
        """GEMINI_MODEL environment variable is respected."""
        monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
        client = GeminiLLMClient(api_key="test_key")
        assert client._model == "gemini-3.5-flash-lite"


# ---------------------------------------------------------------------------
# 8. Financial Safety & Advisory Boundary Tests
# ---------------------------------------------------------------------------

class TestGeminiFinancialSafetyBoundaries:
    """Ensures Gemini recommendations are strictly advisory and never resolve exceptions."""

    def test_never_resolves_or_approves(self, mock_genai_client):
        """Even with AUTO_RECONCILE, investigate_and_persist never changes resolution status."""
        init_db()
        db = SessionLocal()
        try:
            valid_json = json.dumps({
                "recommendation": "AUTO_RECONCILE",
                "confidence": 0.98,
                "reason": "Perfect match identified by Gemini.",
                "risk": "LOW",
            })
            mock_response = MagicMock()
            mock_response.text = valid_json
            mock_genai_client.models.generate_content.return_value = mock_response

            client = GeminiLLMClient(api_key="test_key")
            controller = AIController(client=client)

            unique_id = uuid.uuid4().hex[:8].upper()
            rec_id = f"REC_GEMINI_SAFE_{unique_id}"
            exc_id = f"EXC_GEMINI_SAFE_{unique_id}"
            gw_id = f"GW_SAFE_{unique_id}"
            bnk_id = f"BNK_SAFE_{unique_id}"

            result = ReconciliationResult(
                reconciliation_id=rec_id,
                gateway_transaction_id=gw_id,
                bank_transaction_id=bnk_id,
                match_score=85.0,
                matching_method="EXACT_RULE",
                final_decision="HUMAN_REVIEW",
                discrepancy_amount=0.0,
                is_resolved=False,
            )
            exception = ReconciliationException(
                exception_id=exc_id,
                reconciliation_id=rec_id,
                transaction_id=gw_id,
                category="AMOUNT_MISMATCH",
                severity="LOW",
                status="OPEN",
            )
            db.add(result)
            db.add(exception)
            db.commit()

            ai_res = controller.investigate_and_persist(db, result, exception=exception)
            db.commit()

            # Result is advisory only
            reloaded_result = db.query(ReconciliationResult).filter_by(reconciliation_id=rec_id).first()
            assert reloaded_result.is_resolved is False
            assert reloaded_result.ai_recommendation == "AUTO_RECONCILE"
            assert reloaded_result.matching_method == "AI_REASONING"

            # Exception status remains OPEN
            reloaded_exc = db.query(ReconciliationException).filter_by(exception_id=exc_id).first()
            assert reloaded_exc.status == "OPEN"
            assert reloaded_exc.status not in ("APPROVED", "RESOLVED")
        finally:
            try:
                db.query(AuditLog).filter(AuditLog.entity_id == rec_id).delete(synchronize_session=False)
                db.query(ReconciliationException).filter(ReconciliationException.exception_id == exc_id).delete(synchronize_session=False)
                db.query(ReconciliationResult).filter(ReconciliationResult.reconciliation_id == rec_id).delete(synchronize_session=False)
                db.commit()
            except Exception:
                pass
            db.close()
