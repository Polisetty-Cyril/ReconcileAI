"""
ReconcileAI - AI Controller Pydantic Schemas (Phase 8)

Defines the structured output contract for the AI Finance Controller /
Reasoning Agent.  Every reasoning path (LLM or heuristic) must produce
a result that conforms to AIControllerResult before it may be persisted
or returned to a caller.
"""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Allowed enum values
# ---------------------------------------------------------------------------

ALLOWED_RECOMMENDATIONS: frozenset[str] = frozenset(
    {"AUTO_RECONCILE", "REVIEW", "ESCALATE", "EXCEPTION"}
)

ALLOWED_RISKS: frozenset[str] = frozenset(
    {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
)


# ---------------------------------------------------------------------------
# Main schema
# ---------------------------------------------------------------------------

class AIControllerResult(BaseModel):
    """
    Structured output produced by the Phase 8 AI Finance Controller.

    Fields
    ------
    recommendation : str
        Advisory action for human reviewers.
        Allowed values: AUTO_RECONCILE | REVIEW | ESCALATE | EXCEPTION

    confidence : float
        Reasoning confidence in the range [0.0, 1.0].
        Values below 0.65 must never produce AUTO_RECONCILE (enforced below).

    reason : str
        Human-readable explanation of the recommendation and evidence used.

    risk : str
        Financial risk level of the discrepancy.
        Allowed values: LOW | MEDIUM | HIGH | CRITICAL
    """

    recommendation: str = Field(
        ...,
        description="Advisory action: AUTO_RECONCILE | REVIEW | ESCALATE | EXCEPTION",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Reasoning confidence in [0.0, 1.0]",
    )
    reason: str = Field(
        ...,
        min_length=1,
        description="Human-readable explanation of the recommendation",
    )
    risk: str = Field(
        ...,
        description="Financial risk level: LOW | MEDIUM | HIGH | CRITICAL",
    )

    # ------------------------------------------------------------------
    # Field-level validators
    # ------------------------------------------------------------------

    @field_validator("recommendation")
    @classmethod
    def recommendation_must_be_valid(cls, v: str) -> str:
        normalized = v.strip().upper()
        if normalized not in ALLOWED_RECOMMENDATIONS:
            raise ValueError(
                f"Invalid recommendation '{v}'. "
                f"Must be one of: {sorted(ALLOWED_RECOMMENDATIONS)}"
            )
        return normalized

    @field_validator("risk")
    @classmethod
    def risk_must_be_valid(cls, v: str) -> str:
        normalized = v.strip().upper()
        if normalized not in ALLOWED_RISKS:
            raise ValueError(
                f"Invalid risk '{v}'. "
                f"Must be one of: {sorted(ALLOWED_RISKS)}"
            )
        return normalized

    @field_validator("confidence")
    @classmethod
    def confidence_must_be_in_range(cls, v: float) -> float:
        return round(max(0.0, min(1.0, float(v))), 4)

    # ------------------------------------------------------------------
    # Cross-field safety rule
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def enforce_confidence_safety_rule(self) -> "AIControllerResult":
        """
        Safety rule: confidence < 0.65 must never produce AUTO_RECONCILE.
        If this combination slips through (e.g. from an LLM), override to REVIEW.
        """
        if self.recommendation == "AUTO_RECONCILE" and self.confidence < 0.65:
            object.__setattr__(self, "recommendation", "REVIEW")
            object.__setattr__(
                self,
                "reason",
                f"[Safety override] Confidence {self.confidence:.2f} is below "
                f"the 0.65 threshold for AUTO_RECONCILE. Downgraded to REVIEW. "
                f"Original reason: {self.reason}",
            )
        return self

    def to_db_dict(self) -> dict:
        """Returns a flat dict suitable for writing to ORM columns."""
        return {
            "ai_recommendation": self.recommendation,
            "ai_confidence": round(self.confidence * 100, 2),  # stored 0-100
            "ai_reasoning": self.reason,
        }
