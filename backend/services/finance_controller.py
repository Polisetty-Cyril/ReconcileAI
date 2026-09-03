"""
ReconcileAI - Finance Controller (Orchestration Layer)
Central orchestration coordinator for the ReconcileAI financial reconciliation lifecycle.

Phase: Orchestration Skeleton (Step 1)
Scope:
  Observe -> Deterministic Reconcile -> Return reconciliation outcome.

Safety Invariants:
- Does NOT duplicate deterministic rules, clustering, or scoring logic.
- Does NOT mutate transaction amounts, currencies, or source statuses.
- Does NOT invoke FuzzyMatchEngine (Phase 7) or AIController/Gemini (Phase 8).
- Does NOT invoke SLAOrchestrator or modify exception statuses.
- Does NOT autonomously approve, reject, or resolve exceptions.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Union
from sqlalchemy.orm import Session

from backend.models.transaction import Transaction
from backend.models.reconciliation import ReconciliationResult
from backend.models.exception import ReconciliationException
from backend.schemas.transaction import CanonicalTransaction
from backend.schemas.ai_controller import AIControllerResult
from backend.services.reconciliation import DeterministicReconciliationEngine
from backend.services.fuzzy_matcher import FuzzyMatchEngine, FuzzyMatchResult
from backend.services.ai_controller import AIController
from backend.services.audit_service import AuditService

logger = logging.getLogger(__name__)


class FinanceController:
    """
    Central orchestration controller for financial reconciliation workflows.

    Coordinates the lifecycle of transactions through observation, deterministic
    reconciliation, and in subsequent phases: anomaly detection, fuzzy matching,
    AI reasoning, SLA management, and human review routing.
    """

    def __init__(
        self,
        db: Optional[Session] = None,
        engine: Optional[DeterministicReconciliationEngine] = None,
        fuzzy_engine: Optional[FuzzyMatchEngine] = None,
        ai_controller: Optional[AIController] = None,
    ) -> None:
        """
        Initializes the Finance Controller.

        Parameters
        ----------
        db : Optional[Session]
            SQLAlchemy database session for persistence operations.
        engine : Optional[DeterministicReconciliationEngine]
            Deterministic reconciliation engine instance (injected for testing or configuration).
        fuzzy_engine : Optional[FuzzyMatchEngine]
            Fuzzy matching engine instance (injected for testing or configuration).
        ai_controller : Optional[AIController]
            AI controller instance (injected for testing or configuration).
        """
        self.db = db
        self.engine = engine or DeterministicReconciliationEngine()
        self.fuzzy_engine = fuzzy_engine or FuzzyMatchEngine()
        self.ai_controller = ai_controller or AIController(fuzzy_engine=self.fuzzy_engine)

    def reconcile(
        self,
        transactions: Optional[List[Union[Transaction, CanonicalTransaction]]] = None,
        db: Optional[Session] = None,
        persist: bool = False,
    ) -> Dict[str, Any]:
        """
        Executes Stage 1 of the Finance Controller workflow:
        Observe -> Deterministic Reconcile -> Return reconciliation outcome.

        Parameters
        ----------
        transactions : Optional[List[Union[Transaction, CanonicalTransaction]]]
            List of transaction objects to reconcile. If None and a database session
            is available, transactions are loaded from the database.
        db : Optional[Session]
            SQLAlchemy database session. If provided, overrides self.db.
        persist : bool
            If True, persists reconciliation results and exceptions to the database
            and commits atomically via engine.run_reconciliation_pipeline().
            If False, performs pure in-memory reconciliation via engine.reconcile_transactions().

        Returns
        -------
        Dict[str, Any]
            Summary dictionary containing:
            - total_clusters: int
            - total_reconciled: int
            - total_review: int
            - total_exceptions: int
            - auto_reconciled_rate: float
            - results: List[ReconciliationResult]
            - exceptions: List[ReconciliationException]
        """
        session = db or self.db

        if persist:
            if session is None:
                raise ValueError("A database session (db) is required when persist=True.")
            # engine.run_reconciliation_pipeline loads from db if transactions is None,
            # reconciles in memory once, persists results & exceptions, and commits.
            return self.engine.run_reconciliation_pipeline(session, transactions=transactions)

        # In-memory execution
        if transactions is None:
            if session is None:
                raise ValueError(
                    "Must provide either 'transactions' list or an active database session 'db'."
                )
            transactions = session.query(Transaction).all()

        return self.engine.reconcile_transactions(transactions)

    def run_stage1(
        self,
        transactions: Optional[List[Union[Transaction, CanonicalTransaction]]] = None,
        db: Optional[Session] = None,
        persist: bool = False,
    ) -> Dict[str, Any]:
        """
        Alias for reconcile() representing the Stage-1 deterministic orchestration boundary.
        """
        return self.reconcile(transactions=transactions, db=db, persist=persist)

    def investigate_with_fuzzy(
        self,
        result: Optional[ReconciliationResult] = None,
        gateway_txn: Optional[Any] = None,
        bank_txn: Optional[Any] = None,
        candidate_banks: Optional[List[Any]] = None,
    ) -> Optional[FuzzyMatchResult]:
        """
        Investigates an unresolved transaction or candidate pair using RapidFuzz string similarity.

        Safety Invariants:
        - Investigation-only boundary: returns FuzzyMatchResult evidence without financial mutations.
        - Does NOT modify ReconciliationResult fields or mark is_resolved=True.
        - Does NOT create, approve, reject, or resolve ReconciliationException records.
        - Does NOT persist records to the database.
        - Does NOT invoke AIController, Gemini, or any LLM.

        Parameters
        ----------
        result : Optional[ReconciliationResult]
            Prior reconciliation result, if available. If result is AUTO_RECONCILED,
            fuzzy matching is bypassed immediately (returns None).
        gateway_txn : Optional[Any]
            Gateway transaction object to match.
        bank_txn : Optional[Any]
            Specific Bank transaction object for pairwise scoring.
        candidate_banks : Optional[List[Any]]
            Candidate Bank transactions to search through.

        Returns
        -------
        Optional[FuzzyMatchResult]
            The outcome of the fuzzy comparison, or None if bypassed.
        """
        # Path A: Exact-match fast path
        if result is not None and getattr(result, "final_decision", None) == "AUTO_RECONCILED":
            return None

        # Path B: Pairwise comparison
        if gateway_txn is not None and bank_txn is not None:
            return self.fuzzy_engine.score_pair(gateway_txn, bank_txn)

        # Path C: Candidate search
        if gateway_txn is not None and candidate_banks is not None:
            matches = self.fuzzy_engine.find_best_candidates([gateway_txn], candidate_banks)
            return matches[0] if matches else None

        raise ValueError(
            "Must provide either (gateway_txn, bank_txn) for pairwise investigation "
            "or (gateway_txn, candidate_banks) for candidate investigation."
        )

    def investigate_with_ai(
        self,
        result: ReconciliationResult,
        exception: Optional[ReconciliationException] = None,
        fuzzy_result: Optional[Union[Dict[str, Any], FuzzyMatchResult]] = None,
        gateway_txn: Optional[Any] = None,
        bank_txn: Optional[Any] = None,
        candidate_banks: Optional[List[Any]] = None,
        persist: bool = False,
        db: Optional[Session] = None,
    ) -> AIControllerResult:
        """
        Investigates a reconciliation result using AI reasoning (Phase 8), incorporating
        deterministic exception signals and optional Phase 7 fuzzy matching evidence.

        Safety Invariants:
        - Advisory only: produces recommendations for human reviewers.
        - Does NOT set ReconciliationResult.is_resolved = True.
        - Does NOT approve or reject ReconciliationException records or change their status.
        - Does NOT alter transaction amounts, currencies, or ledger balances.
        - Does NOT commit database transactions internally.

        Parameters
        ----------
        result : ReconciliationResult
            The deterministic reconciliation result to investigate (required).
        exception : Optional[ReconciliationException]
            Associated exception, if one was generated.
        fuzzy_result : Optional[Union[Dict[str, Any], FuzzyMatchResult]]
            Pre-computed fuzzy match result, if available.
        gateway_txn : Optional[Any]
            Gateway transaction object for on-demand fuzzy investigation.
        bank_txn : Optional[Any]
            Specific Bank transaction object for on-demand pairwise fuzzy investigation.
        candidate_banks : Optional[List[Any]]
            Candidate Bank transactions for on-demand candidate fuzzy investigation.
        persist : bool
            If True, writes AI recommendation fields to the database and queues an AuditLog entry
            via AIController.persist_result(). Does NOT commit.
        db : Optional[Session]
            SQLAlchemy database session for persistence operations.

        Returns
        -------
        AIControllerResult
            The validated AI recommendation, confidence, reason, and risk.
        """
        if result is None:
            raise ValueError("ReconciliationResult must be provided for AI investigation.")

        # Fast path: deterministic AUTO_RECONCILED bypasses fuzzy and LLM
        if getattr(result, "final_decision", None) == "AUTO_RECONCILED":
            ai_res = self.ai_controller.investigate(result, exception=exception, fuzzy_result=None)
            if persist:
                session = db or self.db
                if session is None:
                    raise ValueError("A database session (db) is required when persist=True.")
                self.ai_controller.persist_result(
                    session,
                    result,
                    ai_res,
                    exception=exception,
                    fuzzy_result=None,
                )
            return ai_res

        # On-demand fuzzy investigation if fuzzy_result was not supplied
        if fuzzy_result is None and (
            (gateway_txn is not None and bank_txn is not None)
            or (gateway_txn is not None and candidate_banks is not None)
        ):
            fuzzy_result = self.investigate_with_fuzzy(
                result=result,
                gateway_txn=gateway_txn,
                bank_txn=bank_txn,
                candidate_banks=candidate_banks,
            )

        # Compute AI reasoning
        ai_result = self.ai_controller.investigate(
            result,
            exception=exception,
            fuzzy_result=fuzzy_result,
        )

        # Handle persistence if requested
        if persist:
            session = db or self.db
            if session is None:
                raise ValueError("A database session (db) is required when persist=True.")

            # Record FUZZY_INVESTIGATED when fuzzy investigation was performed
            # (bypassed for AUTO_RECONCILED)
            if fuzzy_result is not None and getattr(result, "final_decision", "") != "AUTO_RECONCILED":
                composite_score = getattr(fuzzy_result, "composite_score", None)
                decision = getattr(fuzzy_result, "decision", None)
                if composite_score is None and isinstance(fuzzy_result, dict):
                    composite_score = fuzzy_result.get("composite_score")
                if decision is None and isinstance(fuzzy_result, dict):
                    decision = fuzzy_result.get("decision")

                AuditService(db=session).log_action(
                    actor="FINANCE_CONTROLLER",
                    action="FUZZY_INVESTIGATED",
                    entity="RECONCILIATION",
                    entity_id=getattr(result, "gateway_transaction_id", None) or getattr(result, "bank_transaction_id", None) or getattr(result, "reconciliation_id", "UNKNOWN"),
                    new_value=json.dumps({
                        "reconciliation_id": getattr(result, "reconciliation_id", None),
                        "gateway_transaction_id": getattr(result, "gateway_transaction_id", None),
                        "bank_transaction_id": getattr(result, "bank_transaction_id", None),
                        "composite_score": composite_score,
                        "fuzzy_decision": decision,
                    }),
                    reason=f"Fuzzy investigation completed: score={composite_score}, decision={decision}",
                    commit=False,
                )

            self.ai_controller.persist_result(
                session,
                result,
                ai_result,
                exception=exception,
                fuzzy_result=fuzzy_result,
            )

        return ai_result

    def reconcile_and_investigate(
        self,
        transactions: Optional[List[Union[Transaction, CanonicalTransaction]]] = None,
        db: Optional[Session] = None,
        persist: bool = False,
    ) -> Dict[str, Any]:
        """
        Executes the end-to-end reconciliation and investigation workflow:
        Observe -> Deterministic Reconcile -> Detect Anomaly -> Fuzzy Investigate -> AI Reason -> Enriched Summary.

        Composes existing reconciliation, fuzzy investigation, and AI reasoning primitives
        without duplicating logic or mutating financial invariants.

        Safety Invariants:
        - Advisory only: AI recommendations never approve or reject exceptions.
        - Unresolved discrepancies strictly retain is_resolved=False.
        - Exception status strictly remains OPEN.
        - Transaction amounts, currencies, and balances remain untouched.
        - Does NOT commit database transactions internally.

        Parameters
        ----------
        transactions : Optional[List[Union[Transaction, CanonicalTransaction]]]
            Transactions to reconcile. If None and db is available, loaded from db.
        db : Optional[Session]
            SQLAlchemy database session for persistence operations.
        persist : bool
            If True, stages results, exceptions, AI fields, and AuditLog entries in the session.
            Does NOT commit inside FinanceController.

        Returns
        -------
        Dict[str, Any]
            Deterministic reconciliation summary enriched with "ai_results": List[AIControllerResult].
        """
        session = db or self.db
        if persist and session is None:
            raise ValueError("A database session (db) is required when persist=True.")

        # Step 1: Run deterministic reconciliation
        summary = self.reconcile(transactions=transactions, db=session, persist=persist)

        # Build transaction lookups using actual identifiers
        if transactions is None and session is not None:
            all_txns: List[Any] = session.query(Transaction).all()
        else:
            all_txns = list(transactions or [])

        txn_by_id: Dict[str, Any] = {
            t.transaction_id: t
            for t in all_txns
            if getattr(t, "transaction_id", None)
        }
        candidate_banks = [
            t for t in all_txns
            if getattr(t, "source", "").upper() == "BANK"
        ]

        # Index exceptions by reconciliation_id (safe, non-positional correlation)
        exceptions = summary.get("exceptions", [])
        exceptions_by_recon_id: Dict[str, ReconciliationException] = {
            exc.reconciliation_id: exc
            for exc in exceptions
            if getattr(exc, "reconciliation_id", None)
        }

        # Step 2: Process every reconciliation result
        ai_results: List[AIControllerResult] = []
        for result in summary.get("results", []):
            if getattr(result, "final_decision", None) == "AUTO_RECONCILED":
                # Step 2A: Exact match fast path
                ai_res = self.investigate_with_ai(
                    result=result,
                    persist=persist,
                    db=session,
                )
            else:
                # Step 2B: Unresolved / Human Review discrepancy
                recon_id = getattr(result, "reconciliation_id", None)
                exc = exceptions_by_recon_id.get(recon_id) if recon_id else None

                gw_id = getattr(result, "gateway_transaction_id", None)
                bnk_id = getattr(result, "bank_transaction_id", None)

                gw_txn = txn_by_id.get(gw_id) if gw_id else None
                bnk_txn = txn_by_id.get(bnk_id) if bnk_id else None

                # Steps 3 & 4: Fuzzy investigation & AI reasoning via investigate_with_ai
                ai_res = self.investigate_with_ai(
                    result=result,
                    exception=exc,
                    gateway_txn=gw_txn,
                    bank_txn=bnk_txn,
                    candidate_banks=candidate_banks if (gw_txn and not bnk_txn) else None,
                    persist=persist,
                    db=session,
                )

            ai_results.append(ai_res)

        # Step 6: Return enriched summary
        summary["ai_results"] = ai_results
        return summary
