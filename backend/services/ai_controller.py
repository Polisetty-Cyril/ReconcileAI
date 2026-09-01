"""
ReconcileAI - AI Finance Controller / Reasoning Agent (Phase 8)

Sits AFTER Phase 6 deterministic reconciliation and Phase 7 fuzzy matching.
Consumes their structured output, investigates available evidence, and
produces an AIControllerResult containing:
    recommendation, confidence, reason, risk

Architectural boundaries
------------------------
* Does NOT replace Phase 6 or Phase 7.
* Does NOT issue refunds, move money, or change ledger balances.
* Does NOT autonomously set ReconciliationException.status to APPROVED/RESOLVED.
* Does NOT set ReconciliationResult.is_resolved = True autonomously.
* Recommendations are advisory only — human review is always the final step.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models.audit import AuditLog
from backend.models.exception import ReconciliationException
from backend.models.reconciliation import ReconciliationResult
from backend.schemas.ai_controller import AIControllerResult
from backend.services.llm_client import (
    BaseLLMClient,
    HeuristicLLMClient,
    get_llm_client,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Evidence collector
# ---------------------------------------------------------------------------

def _collect_evidence(
    result: ReconciliationResult,
    exception: Optional[ReconciliationException] = None,
    fuzzy_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Gathers all available signals into a flat evidence dictionary.
    This dict is passed to the LLM client and drives the heuristic engine.
    """
    evidence: Dict[str, Any] = {
        # Phase 6 reconciliation signals
        "reconciliation_id":  getattr(result, "reconciliation_id", None),
        "gateway_txn_id":     getattr(result, "gateway_transaction_id", None),
        "bank_txn_id":        getattr(result, "bank_transaction_id", None),
        "erp_invoice_id":     getattr(result, "erp_invoice_id", None),
        "match_score":        getattr(result, "match_score", None),
        "matching_method":    getattr(result, "matching_method", None),
        "phase6_decision":    getattr(result, "final_decision", None),
        "discrepancy_amount": getattr(result, "discrepancy_amount", 0.0),
    }

    # Phase 6 exception signals (primary evidence)
    if exception is not None:
        evidence.update({
            "exception_id":       getattr(exception, "exception_id", None),
            "category":           getattr(exception, "category", "UNKNOWN"),
            "severity":           getattr(exception, "severity", "MEDIUM"),
            "difference_amount":  getattr(exception, "difference_amount", 0.0),
        })
    else:
        # No explicit exception — derive lightweight evidence from the result
        evidence.setdefault("category", "UNKNOWN")
        evidence.setdefault("severity", "MEDIUM")
        evidence.setdefault("difference_amount", evidence.get("discrepancy_amount", 0.0))

    # Phase 7 fuzzy signals (supplementary)
    if fuzzy_result is not None:
        evidence.update({
            "fuzzy_decision":         fuzzy_result.get("decision"),
            "fuzzy_composite_score":  fuzzy_result.get("composite_score"),
            "fuzzy_amount_diff":      fuzzy_result.get("amount_diff"),
            "fuzzy_matched_fields":   fuzzy_result.get("matched_fields"),
        })

    return evidence


# ---------------------------------------------------------------------------
# Response validator
# ---------------------------------------------------------------------------

def _validate_and_build(raw: Dict[str, Any]) -> Optional[AIControllerResult]:
    """
    Converts a raw dict from an LLM or heuristic client into a validated
    AIControllerResult.  Returns None if validation fails so the caller
    can trigger fallback.
    """
    try:
        return AIControllerResult(
            recommendation=str(raw.get("recommendation", "REVIEW")),
            confidence=float(raw.get("confidence", 0.70)),
            reason=str(raw.get("reason", "No reason provided.")),
            risk=str(raw.get("risk", "MEDIUM")),
        )
    except (ValidationError, TypeError, ValueError) as exc:
        logger.warning("AIControllerResult validation failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Main controller class
# ---------------------------------------------------------------------------

class AIController:
    """
    Phase 8 AI Finance Controller.

    Usage
    -----
    controller = AIController()

    # Investigate a single reconciliation result (+ optional exception + fuzzy)
    ai_result = controller.investigate(result, exception=exc, fuzzy_result=fuzz)

    # Investigate and persist to DB in one step
    ai_result = controller.investigate_and_persist(db, result, exception=exc)

    # Process an entire reconciliation summary (from Phase 6)
    controller.process_reconciliation_summary(db, summary)
    """

    def __init__(self, client: Optional[BaseLLMClient] = None) -> None:
        """
        Parameters
        ----------
        client : BaseLLMClient, optional
            Inject a specific client (useful for testing).
            If None, the factory selects based on settings.
        """
        if client is not None:
            self._client = client
        else:
            self._client = get_llm_client(
                provider=settings.LLM_PROVIDER,
                api_key=settings.LLM_API_KEY,
            )

    # ------------------------------------------------------------------
    # Core investigation method
    # ------------------------------------------------------------------

    def investigate(
        self,
        result: ReconciliationResult,
        exception: Optional[ReconciliationException] = None,
        fuzzy_result: Optional[Dict[str, Any]] = None,
    ) -> AIControllerResult:
        """
        Investigates a reconciliation result and returns a validated
        AIControllerResult.  Never modifies the database.

        Falls back to HeuristicLLMClient automatically if:
        - The primary client raises any exception
        - The response fails schema validation
        - confidence < 0.65 with AUTO_RECONCILE (schema safety rule)
        """
        # Phase 6 AUTO_RECONCILED — respect the deterministic decision
        if getattr(result, "final_decision", "") == "AUTO_RECONCILED":
            return AIControllerResult(
                recommendation="AUTO_RECONCILE",
                confidence=1.0,
                reason=(
                    "Phase 6 deterministic engine confirmed AUTO_RECONCILED "
                    "with 100% match score. No further investigation required."
                ),
                risk="LOW",
            )

        evidence = _collect_evidence(result, exception, fuzzy_result)

        # Primary client attempt
        ai_result = self._call_client(self._client, evidence)

        # Fallback to heuristic if primary failed or returned invalid data
        if ai_result is None:
            logger.warning(
                "Primary client failed for reconciliation_id=%s. "
                "Falling back to HeuristicLLMClient.",
                evidence.get("reconciliation_id"),
            )
            heuristic = HeuristicLLMClient()
            raw = heuristic.reason(evidence)
            ai_result = _validate_and_build(raw)

        # Last-resort hardcoded safe default (should never be reached)
        if ai_result is None:
            ai_result = AIControllerResult(
                recommendation="REVIEW",
                confidence=0.60,
                reason=(
                    "All reasoning paths failed. Defaulting to REVIEW for "
                    "mandatory human inspection."
                ),
                risk="HIGH",
            )

        return ai_result

    # ------------------------------------------------------------------
    # Persist to DB
    # ------------------------------------------------------------------

    def investigate_and_persist(
        self,
        db: Session,
        result: ReconciliationResult,
        exception: Optional[ReconciliationException] = None,
        fuzzy_result: Optional[Dict[str, Any]] = None,
    ) -> AIControllerResult:
        """
        Investigates the result, writes AI fields to the DB, and appends
        an AuditLog entry.  Does NOT commit — caller controls the transaction.

        Safety rules enforced here:
        - is_resolved is never set to True by Phase 8.
        - ReconciliationException.status is never set to APPROVED/RESOLVED.
        """
        ai_result = self.investigate(result, exception, fuzzy_result)

        # Determine matching_method label
        client_name = type(self._client).__name__
        if isinstance(self._client, HeuristicLLMClient):
            method_label = "HEURISTIC_FALLBACK"
        else:
            method_label = "AI_REASONING"

        # Write back to ReconciliationResult
        result.ai_recommendation = ai_result.recommendation
        result.ai_confidence = round(ai_result.confidence * 100, 2)  # stored 0-100
        result.ai_reasoning = ai_result.reason
        result.matching_method = method_label
        db.add(result)

        # Write ai_explanation to the exception (never change status autonomously)
        if exception is not None:
            exception.ai_explanation = ai_result.reason
            db.add(exception)

        # Append AuditLog entry
        audit = AuditLog(
            audit_id=f"AUD_AI_{uuid.uuid4().hex[:12].upper()}",
            timestamp=datetime.now(timezone.utc),
            actor="AI_CONTROLLER",
            action="AI_REASONED",
            entity="RECONCILIATION",
            entity_id=getattr(result, "reconciliation_id", "UNKNOWN"),
            old_value=getattr(result, "final_decision", None),
            new_value=ai_result.recommendation,
            reason=ai_result.reason[:500],  # truncate for audit column
        )
        db.add(audit)

        return ai_result

    # ------------------------------------------------------------------
    # Batch processing (consumes Phase 6 summary dict directly)
    # ------------------------------------------------------------------

    def process_reconciliation_summary(
        self,
        db: Session,
        summary: Dict[str, Any],
        fuzzy_results: Optional[List[Dict[str, Any]]] = None,
    ) -> List[AIControllerResult]:
        """
        Processes an entire Phase 6 reconciliation summary in one call.

        Parameters
        ----------
        db : Session
        summary : dict
            Output of DeterministicReconciliationEngine.reconcile_transactions()
            or run_reconciliation_pipeline().  Expected keys: results, exceptions.
        fuzzy_results : list of FuzzyMatchResult dicts, optional
            Indexed positionally against gateway_transaction_id if provided.

        Returns
        -------
        List of AIControllerResult, one per reconciliation cluster.
        """
        results: List[ReconciliationResult] = summary.get("results", [])
        exceptions: List[ReconciliationException] = summary.get("exceptions", [])

        # Build exception lookup by reconciliation_id
        exc_by_recon: Dict[str, ReconciliationException] = {
            exc.reconciliation_id: exc
            for exc in exceptions
            if exc.reconciliation_id
        }

        # Build fuzzy lookup by gateway_txn_id if provided
        fuzzy_by_gw: Dict[str, Dict[str, Any]] = {}
        if fuzzy_results:
            for fr in fuzzy_results:
                gw_id = (
                    fr.get("gateway_txn_id")
                    if isinstance(fr, dict)
                    else getattr(fr, "gateway_txn_id", None)
                )
                if gw_id:
                    fuzzy_by_gw[gw_id] = (
                        fr if isinstance(fr, dict) else fr.__dict__
                    )

        ai_results: List[AIControllerResult] = []
        for recon_result in results:
            exc = exc_by_recon.get(getattr(recon_result, "reconciliation_id", ""))
            fuzz = fuzzy_by_gw.get(
                getattr(recon_result, "gateway_transaction_id", "") or ""
            )
            ai_result = self.investigate_and_persist(db, recon_result, exc, fuzz)
            ai_results.append(ai_result)

        db.commit()
        return ai_results

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    @staticmethod
    def _call_client(
        client: BaseLLMClient,
        evidence: Dict[str, Any],
    ) -> Optional[AIControllerResult]:
        """
        Calls the client's reason() method and validates the response.
        Returns None on any failure so the caller can fall back.
        """
        try:
            raw = client.reason(evidence)
            return _validate_and_build(raw)
        except Exception as exc:
            logger.warning("LLM client raised an exception: %s", exc)
            return None
