"""
ReconcileAI - LLM Client Interface & Provider Factory (Phase 8)

Provides:
  BaseLLMClient      — abstract interface that every provider must implement.
  HeuristicLLMClient — deterministic, offline, zero-dependency fallback.
  get_llm_client()   — factory that selects the correct client at runtime.

Design rules
------------
* Provider SDKs (openai, groq, google-generativeai) are lazy-imported inside
  the concrete client constructors.  They are NEVER imported at module level,
  so the rest of the application works without any of them installed.
* LLM_API_KEY is never logged or returned in any response.
* The heuristic client works with no network, no API key, no SDK.
* Any construction or call failure on a real provider silently falls back
  to HeuristicLLMClient.
"""

from __future__ import annotations

import json
import logging
import os
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Evidence dict type alias (for readability)
# ---------------------------------------------------------------------------
EvidenceDict = Dict[str, Any]

# ---------------------------------------------------------------------------
# Allowed output enums (mirrors ai_controller schema)
# ---------------------------------------------------------------------------
ALLOWED_RECOMMENDATIONS = frozenset({"AUTO_RECONCILE", "REVIEW", "ESCALATE", "EXCEPTION"})
ALLOWED_RISKS = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})

REQUIRED_KEYS = ("recommendation", "confidence", "reason", "risk")


# ===========================================================================
# Abstract interface
# ===========================================================================

class BaseLLMClient(ABC):
    """
    Abstract interface that every LLM provider client must implement.
    The single method `reason` takes structured evidence and returns a
    raw result dict.  Validation and persistence happen in ai_controller.py.
    """

    @abstractmethod
    def reason(self, evidence: EvidenceDict) -> Dict[str, Any]:
        """
        Parameters
        ----------
        evidence : dict
            Structured evidence collected by AIController.

        Returns
        -------
        dict with keys: recommendation, confidence (float 0-1), reason, risk
        """
        ...

    def _validate_raw(self, raw: Dict[str, Any]) -> bool:
        """
        Lightweight structural check: all required keys present,
        confidence is numeric, enums are valid strings.
        Returns True if the response is structurally sound.
        """
        try:
            for key in REQUIRED_KEYS:
                if key not in raw:
                    return False
            conf = float(raw["confidence"])
            if not (0.0 <= conf <= 1.0):
                return False
            if str(raw["recommendation"]).upper() not in ALLOWED_RECOMMENDATIONS:
                return False
            if str(raw["risk"]).upper() not in ALLOWED_RISKS:
                return False
            return True
        except (TypeError, ValueError):
            return False


# ===========================================================================
# Heuristic (deterministic) client — always available
# ===========================================================================

class HeuristicLLMClient(BaseLLMClient):
    """
    Deterministic rule-table-based reasoning engine.

    Works completely offline.  No external dependencies.
    Used when:
      - AI_ENABLED is False  (default)
      - LLM_API_KEY is empty
      - Provider SDK is not installed
      - Provider call raises any exception
      - Provider response is malformed
    """

    # Rule table: (category, severity) → (recommendation, confidence, risk)
    # Ordered from most-specific to least-specific.
    _RULES: list[tuple[str, str, str, float, str]] = [
        # (category,                      severity,   recommendation,  confidence, risk)
        ("AMOUNT_MISMATCH",               "CRITICAL", "ESCALATE",      0.95,       "CRITICAL"),
        ("AMOUNT_MISMATCH",               "HIGH",     "REVIEW",         0.90,       "HIGH"),
        ("AMOUNT_MISMATCH",               "MEDIUM",   "REVIEW",         0.85,       "MEDIUM"),
        ("AMOUNT_MISMATCH",               "LOW",      "REVIEW",         0.75,       "LOW"),
        ("MISSING_BANK_TRANSACTION",      "CRITICAL", "ESCALATE",      0.97,       "CRITICAL"),
        ("MISSING_BANK_TRANSACTION",      "HIGH",     "ESCALATE",      0.92,       "HIGH"),
        ("MISSING_BANK_TRANSACTION",      "MEDIUM",   "REVIEW",         0.80,       "MEDIUM"),
        ("MISSING_GATEWAY_TRANSACTION",   "CRITICAL", "ESCALATE",      0.97,       "CRITICAL"),
        ("MISSING_GATEWAY_TRANSACTION",   "HIGH",     "REVIEW",         0.88,       "HIGH"),
        ("MISSING_GATEWAY_TRANSACTION",   "MEDIUM",   "REVIEW",         0.78,       "MEDIUM"),
        ("DUPLICATE_TRANSACTION",         "CRITICAL", "ESCALATE",      0.98,       "CRITICAL"),
        ("DUPLICATE_TRANSACTION",         "HIGH",     "ESCALATE",      0.95,       "CRITICAL"),
        ("DUPLICATE_TRANSACTION",         "MEDIUM",   "ESCALATE",      0.88,       "HIGH"),
        ("DATE_MISMATCH",                 "HIGH",     "REVIEW",         0.85,       "HIGH"),
        ("DATE_MISMATCH",                 "MEDIUM",   "REVIEW",         0.80,       "MEDIUM"),
        ("DATE_MISMATCH",                 "LOW",      "REVIEW",         0.72,       "LOW"),
        ("REFERENCE_MISMATCH",            "HIGH",     "REVIEW",         0.85,       "HIGH"),
        ("REFERENCE_MISMATCH",            "MEDIUM",   "REVIEW",         0.82,       "MEDIUM"),
        ("REFERENCE_MISMATCH",            "LOW",      "REVIEW",         0.72,       "LOW"),
        ("PARTIAL_PAYMENT",               "HIGH",     "REVIEW",         0.88,       "HIGH"),
        ("PARTIAL_PAYMENT",               "MEDIUM",   "REVIEW",         0.85,       "MEDIUM"),
        ("PARTIAL_PAYMENT",               "LOW",      "REVIEW",         0.75,       "LOW"),
        ("FAILED_PAYMENT",                "LOW",      "AUTO_RECONCILE", 0.98,       "LOW"),
        ("FAILED_PAYMENT",                "MEDIUM",   "REVIEW",         0.85,       "MEDIUM"),
        ("ANOMALY",                       "CRITICAL", "ESCALATE",      0.97,       "CRITICAL"),
        ("ANOMALY",                       "HIGH",     "ESCALATE",      0.92,       "HIGH"),
        ("ANOMALY",                       "MEDIUM",   "REVIEW",         0.80,       "MEDIUM"),
        ("EXCEPTION",                     "CRITICAL", "ESCALATE",      0.95,       "CRITICAL"),
        ("EXCEPTION",                     "HIGH",     "ESCALATE",      0.90,       "HIGH"),
        ("EXCEPTION",                     "MEDIUM",   "REVIEW",         0.75,       "MEDIUM"),
    ]

    # Large-amount escalation threshold (INR)
    LARGE_AMOUNT_THRESHOLD: float = 10_000.0

    def reason(self, evidence: EvidenceDict) -> Dict[str, Any]:
        category:  str   = str(evidence.get("category",  "UNKNOWN")).upper()
        severity:  str   = str(evidence.get("severity",  "MEDIUM")).upper()
        diff:      float = float(evidence.get("difference_amount", 0.0))
        has_gw:    bool  = bool(evidence.get("gateway_txn_id"))
        has_bank:  bool  = bool(evidence.get("bank_txn_id"))

        # ── Insufficient evidence guard ────────────────────────────────────
        if not has_gw and not has_bank and category == "UNKNOWN":
            return self._build(
                recommendation="REVIEW",
                confidence=0.60,
                reason=(
                    "Insufficient evidence: no gateway or bank transaction IDs "
                    "available. Flagged for human review."
                ),
                risk="MEDIUM",
            )

        # ── Conflicting evidence detection ─────────────────────────────────
        fuzzy_decision: str = str(evidence.get("fuzzy_decision", "")).upper()
        phase6_decision: str = str(evidence.get("phase6_decision", "")).upper()
        conflicting = (
            fuzzy_decision == "FUZZY_MATCHED"
            and phase6_decision == "HUMAN_REVIEW"
            and category in ("AMOUNT_MISMATCH", "REFERENCE_MISMATCH")
        )

        # ── Rule-table lookup ──────────────────────────────────────────────
        recommendation, confidence, risk = self._lookup(category, severity)

        # ── Large-amount escalation override ──────────────────────────────
        if diff > self.LARGE_AMOUNT_THRESHOLD or severity == "CRITICAL":
            if recommendation not in ("ESCALATE",):
                recommendation = "ESCALATE"
            if risk not in ("CRITICAL",):
                risk = "CRITICAL"
            confidence = max(confidence, 0.92)

        # ── Conflicting-evidence penalty ───────────────────────────────────
        if conflicting:
            confidence = min(confidence, 0.70)
            risk = self._elevate_risk(risk)

        # ── Low-confidence safety rule ─────────────────────────────────────
        if confidence < 0.65 and recommendation == "AUTO_RECONCILE":
            recommendation = "REVIEW"

        # ── Build reason string ────────────────────────────────────────────
        reason = self._build_reason(
            category, severity, diff, recommendation, confidence,
            conflicting, fuzzy_decision, phase6_decision, evidence
        )

        return self._build(recommendation, confidence, reason, risk)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _lookup(self, category: str, severity: str) -> tuple[str, float, str]:
        for cat, sev, rec, conf, risk in self._RULES:
            if cat == category and sev == severity:
                return rec, conf, risk
        # Default fallback
        return "REVIEW", 0.70, "MEDIUM"

    @staticmethod
    def _elevate_risk(risk: str) -> str:
        ladder = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
        idx = ladder.index(risk) if risk in ladder else 1
        return ladder[min(idx + 1, 3)]

    @staticmethod
    def _build(
        recommendation: str,
        confidence: float,
        reason: str,
        risk: str,
    ) -> Dict[str, Any]:
        return {
            "recommendation": recommendation.upper(),
            "confidence": round(confidence, 4),
            "reason": reason,
            "risk": risk.upper(),
        }

    @staticmethod
    def _build_reason(
        category: str,
        severity: str,
        diff: float,
        recommendation: str,
        confidence: float,
        conflicting: bool,
        fuzzy_decision: str,
        phase6_decision: str,
        evidence: EvidenceDict,
    ) -> str:
        parts: list[str] = [
            f"Heuristic analysis: category={category}, severity={severity}.",
        ]
        if diff > 0:
            parts.append(f"Discrepancy amount: ₹{diff:,.2f}.")
        if conflicting:
            parts.append(
                f"Conflicting evidence detected: Phase 7 fuzzy matcher "
                f"returned '{fuzzy_decision}' but Phase 6 classified as "
                f"'{phase6_decision}'. Confidence capped and risk elevated."
            )
        match_score = evidence.get("match_score")
        if match_score is not None:
            parts.append(f"Phase 6 match score: {match_score}.")
        fuzzy_score = evidence.get("fuzzy_composite_score")
        if fuzzy_score is not None:
            parts.append(f"Phase 7 fuzzy composite score: {fuzzy_score:.1f}.")
        parts.append(
            f"Recommendation: {recommendation} "
            f"(confidence={confidence:.2f})."
        )
        return " ".join(parts)


# ===========================================================================
# Real provider stubs — activated only when SDK is installed and key present
# ===========================================================================

class OpenAILLMClient(BaseLLMClient):
    """OpenAI GPT-based reasoning client (lazy SDK import)."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        try:
            import openai  # type: ignore
            self._client = openai.OpenAI(api_key=api_key)
            self._model = model
        except ImportError:
            raise RuntimeError(
                "openai package is not installed. "
                "Install it with: pip install openai"
            )

    def reason(self, evidence: EvidenceDict) -> Dict[str, Any]:
        prompt = _build_prompt(evidence)
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                timeout=15,
            )
            raw_text = response.choices[0].message.content or ""
            return _parse_llm_json(raw_text)
        except Exception as exc:
            logger.warning("OpenAI call failed (%s); falling back to heuristic.", exc)
            return HeuristicLLMClient().reason(evidence)


class GroqLLMClient(BaseLLMClient):
    """Groq-hosted LLM client (lazy SDK import)."""

    def __init__(self, api_key: str, model: str = "llama3-8b-8192") -> None:
        try:
            from groq import Groq  # type: ignore
            self._client = Groq(api_key=api_key)
            self._model = model
        except ImportError:
            raise RuntimeError(
                "groq package is not installed. "
                "Install it with: pip install groq"
            )

    def reason(self, evidence: EvidenceDict) -> Dict[str, Any]:
        prompt = _build_prompt(evidence)
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                timeout=15,
            )
            raw_text = response.choices[0].message.content or ""
            return _parse_llm_json(raw_text)
        except Exception as exc:
            logger.warning("Groq call failed (%s); falling back to heuristic.", exc)
            return HeuristicLLMClient().reason(evidence)


def _sanitize_key(msg: str, secret: Optional[str] = None) -> str:
    """Removes sensitive keys and credentials from log messages."""
    if not msg:
        return ""
    if secret and secret in msg:
        msg = msg.replace(secret, "[REDACTED]")
    msg = re.sub(r"key=[A-Za-z0-9_\-]+", "key=[REDACTED]", msg)
    msg = re.sub(r"AIza[0-9A-Za-z-_]{35}", "[REDACTED]", msg)
    return msg


class GeminiLLMClient(BaseLLMClient):
    """
    Google Gemini reasoning client using official `google-genai` SDK.
    Configured via environment variables or explicit parameters:
      GEMINI_API_KEY  (required: Gemini API key)
      GEMINI_MODEL    (optional: defaults to gemini-2.5-flash)
    """

    DEFAULT_MODEL = "gemini-2.5-flash"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        self._api_key = (
            api_key
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("LLM_API_KEY")
            or ""
        ).strip()

        if not self._api_key:
            raise ValueError("GEMINI_API_KEY is not configured.")

        self._model = (
            model
            or os.getenv("GEMINI_MODEL")
            or self.DEFAULT_MODEL
        ).strip()

        try:
            import sys
            # Compatibility check for test environments simulating missing SDK
            if (
                ("google.genai" in sys.modules and sys.modules["google.genai"] is None)
                or ("google.generativeai" in sys.modules and sys.modules["google.generativeai"] is None)
            ):
                raise ImportError("google-genai package is not installed.")
            from google import genai
            from google.genai import types

            self._client = genai.Client(api_key=self._api_key)
            self._types = types
        except (ImportError, Exception) as exc:
            raise RuntimeError(
                "google-genai package is not installed. "
                "Install it with: pip install google-genai"
            ) from exc

    def __repr__(self) -> str:
        # Prevent secret leakage in object string representation
        return f"GeminiLLMClient(model='{self._model}')"

    def reason(self, evidence: EvidenceDict) -> Dict[str, Any]:
        prompt = _build_prompt(evidence)
        try:
            from backend.schemas.ai_controller import AIControllerResult

            config = self._types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=AIControllerResult,
                temperature=0.1,
            )
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=config,
            )
            raw_text = response.text or ""
            data = _parse_llm_json(raw_text)

            # Validate against structural requirements
            if not self._validate_raw(data):
                logger.warning(
                    "Gemini response failed structural validation; falling back to heuristic."
                )
                return HeuristicLLMClient().reason(evidence)

            # Safety override: confidence < 0.65 must never produce AUTO_RECONCILE
            rec = str(data.get("recommendation", "REVIEW")).upper()
            conf = round(float(data.get("confidence", 0.7)), 4)
            reason = str(data.get("reason", ""))
            risk = str(data.get("risk", "MEDIUM")).upper()

            if rec == "AUTO_RECONCILE" and conf < 0.65:
                rec = "REVIEW"
                reason = (
                    f"[Safety override] Confidence {conf:.2f} is below the "
                    f"0.65 threshold for AUTO_RECONCILE. Downgraded to REVIEW. "
                    f"Original reason: {reason}"
                )

            return {
                "recommendation": rec,
                "confidence": conf,
                "reason": reason,
                "risk": risk,
            }
        except Exception as exc:
            safe_msg = _sanitize_key(str(exc), self._api_key)
            logger.warning(
                "Gemini call failed (%s: %s); falling back to heuristic.",
                type(exc).__name__,
                safe_msg,
            )
            return HeuristicLLMClient().reason(evidence)


# ===========================================================================
# Provider factory
# ===========================================================================

def get_llm_client(provider: str, api_key: Optional[str] = None) -> BaseLLMClient:
    """
    Returns the appropriate LLM client based on settings.

    Falls back to HeuristicLLMClient whenever:
    - provider is "heuristic"
    - api_key is empty / None (and no GEMINI_API_KEY environment variable for gemini)
    - provider SDK is not installed (ImportError / RuntimeError)
    - any construction error occurs
    """
    provider = (provider or "heuristic").strip().lower()
    api_key = (api_key or "").strip()

    if provider == "gemini" and not api_key:
        api_key = os.getenv("GEMINI_API_KEY", "").strip()

    if not api_key or provider == "heuristic":
        return HeuristicLLMClient()

    try:
        if provider == "openai":
            return OpenAILLMClient(api_key)
        if provider == "groq":
            return GroqLLMClient(api_key)
        if provider == "gemini":
            return GeminiLLMClient(api_key=api_key)
    except (RuntimeError, ValueError, Exception) as exc:
        logger.warning(
            "Could not initialize LLM provider '%s' (%s). "
            "Falling back to HeuristicLLMClient.",
            provider,
            exc,
        )

    return HeuristicLLMClient()


# ===========================================================================
# Shared LLM prompt helpers (used by real provider clients)
# ===========================================================================

_SYSTEM_PROMPT = (
    "You are an AI Finance Controller for a payment reconciliation system. "
    "Analyse the provided financial discrepancy evidence and return a JSON "
    "object with exactly four keys: "
    "\"recommendation\" (one of: AUTO_RECONCILE, REVIEW, ESCALATE, EXCEPTION), "
    "\"confidence\" (float 0.0 to 1.0), "
    "\"reason\" (concise human-readable explanation), "
    "\"risk\" (one of: LOW, MEDIUM, HIGH, CRITICAL). "
    "Do NOT issue refunds, move money, or modify any records. "
    "Do NOT autonomously approve or resolve exceptions. "
    "Respond with valid JSON only."
)


def _build_prompt(evidence: EvidenceDict) -> str:
    """Serialises evidence into a compact prompt string for LLM providers."""
    lines = ["Financial discrepancy evidence:"]
    for key, value in evidence.items():
        if value is not None and value != "" and value != []:
            lines.append(f"  {key}: {value}")
    lines.append(
        "\nRespond with a JSON object: "
        "{\"recommendation\": ..., \"confidence\": ..., "
        "\"reason\": ..., \"risk\": ...}"
    )
    return "\n".join(lines)


def _parse_llm_json(raw_text: str) -> Dict[str, Any]:
    """
    Attempts to parse LLM text output as JSON.
    Raises ValueError on any parse failure so callers can fall back.
    """
    text = raw_text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            line for line in lines
            if not line.startswith("```")
        ).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM response is not valid JSON: {exc}") from exc
    return data
