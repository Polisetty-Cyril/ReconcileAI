"""
ReconcileAI - Centralized Audit Service
Provides standard creation, logging, and retrieval of system audit trail entries.
"""

from __future__ import annotations

import logging
import uuid
from typing import List, Optional
from sqlalchemy.orm import Session

from backend.models.audit import AuditLog

logger = logging.getLogger(__name__)


class AuditService:
    """
    Centralized service for managing system audit trail records.
    Coordinates logging and querying of AuditLog entries across financial reconciliation,
    webhooks, AI reasoning, and operator interventions.
    """

    def __init__(self, db: Optional[Session] = None) -> None:
        """
        Initializes the AuditService.

        Parameters
        ----------
        db : Optional[Session]
            SQLAlchemy database session for audit operations. Can be overridden per method call.
        """
        self.db = db

    def log_action(
        self,
        actor: str,
        action: str,
        entity: str,
        entity_id: str,
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
        reason: Optional[str] = None,
        db: Optional[Session] = None,
        commit: bool = False,
    ) -> AuditLog:
        """
        Creates and stages (or commits) an AuditLog record.

        Parameters
        ----------
        actor : str
            Entity or user initiating the action (e.g. SYSTEM, AI_CONTROLLER, HUMAN_OPERATOR).
        action : str
            Action identifier (e.g. TRANSACTION_INGESTED, AUTO_RECONCILED, AI_REASONED).
        entity : str
            Entity domain being audited (e.g. TRANSACTION, RECONCILIATION, EXCEPTION, WEBHOOK).
        entity_id : str
            Unique business identifier for the audited entity.
        old_value : Optional[str]
            State or representation before the action.
        new_value : Optional[str]
            State or representation after the action.
        reason : Optional[str]
            Contextual explanation or justification for the audit event.
        db : Optional[Session]
            Per-call database session override. If None, self.db is used.
        commit : bool
            Whether to commit the session immediately. Default is False.

        Returns
        -------
        AuditLog
            The newly created AuditLog instance.
        """
        session = db or self.db
        if session is None:
            raise ValueError("A database session (db) is required to log an audit action.")

        audit_id = f"AUD_{uuid.uuid4().hex[:12].upper()}"
        entry = AuditLog(
            audit_id=audit_id,
            actor=str(actor),
            action=str(action),
            entity=str(entity),
            entity_id=str(entity_id),
            old_value=str(old_value) if old_value is not None else None,
            new_value=str(new_value) if new_value is not None else None,
            reason=str(reason) if reason is not None else None,
        )

        session.add(entry)
        if commit:
            session.commit()
            session.refresh(entry)

        return entry

    def get_audit_trail(
        self,
        entity: Optional[str] = None,
        entity_id: Optional[str] = None,
        action: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> List[AuditLog]:
        """
        Retrieves AuditLog entries matching optional composable filters.

        Parameters
        ----------
        entity : Optional[str]
            Filter by entity category (e.g. 'EXCEPTION', 'WEBHOOK').
        entity_id : Optional[str]
            Filter by specific entity identifier.
        action : Optional[str]
            Filter by specific action identifier.
        db : Optional[Session]
            Per-call database session override. If None, self.db is used.

        Returns
        -------
        List[AuditLog]
            Audit records ordered chronologically ascending by timestamp and deterministic secondary id.
        """
        session = db or self.db
        if session is None:
            raise ValueError("A database session (db) is required to query audit trails.")

        query = session.query(AuditLog)
        if entity is not None:
            query = query.filter(AuditLog.entity == entity)
        if entity_id is not None:
            query = query.filter(AuditLog.entity_id == entity_id)
        if action is not None:
            query = query.filter(AuditLog.action == action)

        return query.order_by(AuditLog.timestamp.asc(), AuditLog.id.asc()).all()
