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

import logging
from typing import Any, Dict, List, Optional, Union
from sqlalchemy.orm import Session

from backend.models.transaction import Transaction
from backend.schemas.transaction import CanonicalTransaction
from backend.services.reconciliation import DeterministicReconciliationEngine

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
    ) -> None:
        """
        Initializes the Finance Controller.

        Parameters
        ----------
        db : Optional[Session]
            SQLAlchemy database session for persistence operations.
        engine : Optional[DeterministicReconciliationEngine]
            Deterministic reconciliation engine instance (injected for testing or configuration).
        """
        self.db = db
        self.engine = engine or DeterministicReconciliationEngine()

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
