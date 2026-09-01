"""
ReconcileAI - Exception Management Service (Phase 11)
Implements human reviewer decision workflow: querying exception queues, explicit
approval and rejection, atomic database state synchronization, and immutable audit logging.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Tuple
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.models.exception import ReconciliationException
from backend.models.reconciliation import ReconciliationResult
from backend.models.audit import AuditLog

class ExceptionManagementService:
    """Service managing reconciliation exceptions lifecycle and human reviewer decisions."""

    @classmethod
    def list_exceptions(
        cls,
        db: Session,
        status_filter: Optional[str] = None,
        severity_filter: Optional[str] = None,
        category_filter: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Tuple[List[ReconciliationException], int]:
        """
        Retrieves a paginated list of exceptions with optional filtering by status, severity, or category.
        Returns a tuple of (items, total_count).
        """
        query = db.query(ReconciliationException)

        if status_filter:
            query = query.filter(ReconciliationException.status == status_filter.strip().upper())
        if severity_filter:
            query = query.filter(ReconciliationException.severity == severity_filter.strip().upper())
        if category_filter:
            query = query.filter(ReconciliationException.category == category_filter.strip().upper())

        total = query.count()
        items = query.order_by(ReconciliationException.created_at.desc()).offset(offset).limit(limit).all()
        return items, total

    @classmethod
    def get_exception_by_id(
        cls,
        db: Session,
        exception_id: str
    ) -> Optional[ReconciliationException]:
        """
        Fetches a single exception record by its unique exception_id.
        """
        return db.query(ReconciliationException).filter_by(exception_id=exception_id).first()

    @classmethod
    def approve_exception(
        cls,
        db: Session,
        exception_id: str,
        reviewer_id: str = "HUMAN_OPERATOR",
        notes: Optional[str] = None
    ) -> ReconciliationException:
        """
        Executes a human reviewer approval decision for an exception:
        - Sets exception.status = 'APPROVED'
        - Records reviewer_id, notes, and resolved_at
        - Synchronizes associated ReconciliationResult (is_resolved=True, final_decision='MANUAL_APPROVED')
        - Creates an immutable AuditLog entry (action='EXCEPTION_APPROVED')
        - Enforces idempotency and validates against conflicting state transitions
        """
        exception = cls.get_exception_by_id(db, exception_id)
        if not exception:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Exception '{exception_id}' not found."
            )

        # Idempotency check: same reviewer re-approving an already approved exception
        if exception.status == "APPROVED" and exception.resolved_by == reviewer_id:
            return exception

        # Conflict check: cannot approve an already rejected exception
        if exception.status == "REJECTED":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot approve exception '{exception_id}': already REJECTED by {exception.resolved_by}."
            )

        # Conflict check: cannot overwrite approval from a different reviewer
        if exception.status == "APPROVED" and exception.resolved_by != reviewer_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot approve exception '{exception_id}': already APPROVED by different reviewer '{exception.resolved_by}'."
            )

        old_status = exception.status
        resolved_time = datetime.now(timezone.utc)

        # 1. Update Exception state
        exception.status = "APPROVED"
        exception.reviewer_notes = notes
        exception.resolved_by = reviewer_id
        exception.resolved_at = resolved_time

        # 2. Synchronize linked ReconciliationResult if present
        if exception.reconciliation_id:
            recon = db.query(ReconciliationResult).filter_by(reconciliation_id=exception.reconciliation_id).first()
            if recon:
                recon.is_resolved = True
                recon.final_decision = "MANUAL_APPROVED"

        # 3. Create immutable AuditLog entry
        audit_entry = AuditLog(
            audit_id=f"AUD_EXC_APP_{uuid.uuid4().hex[:12].upper()}",
            timestamp=resolved_time,
            actor=reviewer_id,
            action="EXCEPTION_APPROVED",
            entity="EXCEPTION",
            entity_id=exception_id,
            old_value=json.dumps({"status": old_status}),
            new_value=json.dumps({
                "status": "APPROVED",
                "resolved_by": reviewer_id,
                "reviewer_notes": notes
            }),
            reason=notes or "Approved by human reviewer"
        )
        db.add(audit_entry)

        # Commit all state transitions atomically
        db.commit()
        db.refresh(exception)
        return exception

    @classmethod
    def reject_exception(
        cls,
        db: Session,
        exception_id: str,
        reviewer_id: str = "HUMAN_OPERATOR",
        notes: Optional[str] = None
    ) -> ReconciliationException:
        """
        Executes a human reviewer rejection decision for an exception:
        - Sets exception.status = 'REJECTED'
        - Records reviewer_id, notes, and resolved_at
        - Synchronizes associated ReconciliationResult (is_resolved=True, final_decision='MANUAL_REJECTED')
        - Creates an immutable AuditLog entry (action='EXCEPTION_REJECTED')
        - Enforces idempotency and validates against conflicting state transitions
        """
        exception = cls.get_exception_by_id(db, exception_id)
        if not exception:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Exception '{exception_id}' not found."
            )

        # Idempotency check: same reviewer re-rejecting an already rejected exception
        if exception.status == "REJECTED" and exception.resolved_by == reviewer_id:
            return exception

        # Conflict check: cannot reject an already approved exception
        if exception.status == "APPROVED":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot reject exception '{exception_id}': already APPROVED by {exception.resolved_by}."
            )

        # Conflict check: cannot overwrite rejection from a different reviewer
        if exception.status == "REJECTED" and exception.resolved_by != reviewer_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot reject exception '{exception_id}': already REJECTED by different reviewer '{exception.resolved_by}'."
            )

        old_status = exception.status
        resolved_time = datetime.now(timezone.utc)

        # 1. Update Exception state
        exception.status = "REJECTED"
        exception.reviewer_notes = notes
        exception.resolved_by = reviewer_id
        exception.resolved_at = resolved_time

        # 2. Synchronize linked ReconciliationResult if present
        if exception.reconciliation_id:
            recon = db.query(ReconciliationResult).filter_by(reconciliation_id=exception.reconciliation_id).first()
            if recon:
                recon.is_resolved = True
                recon.final_decision = "MANUAL_REJECTED"

        # 3. Create immutable AuditLog entry
        audit_entry = AuditLog(
            audit_id=f"AUD_EXC_REJ_{uuid.uuid4().hex[:12].upper()}",
            timestamp=resolved_time,
            actor=reviewer_id,
            action="EXCEPTION_REJECTED",
            entity="EXCEPTION",
            entity_id=exception_id,
            old_value=json.dumps({"status": old_status}),
            new_value=json.dumps({
                "status": "REJECTED",
                "resolved_by": reviewer_id,
                "reviewer_notes": notes
            }),
            reason=notes or "Rejected by human reviewer"
        )
        db.add(audit_entry)

        # Commit all state transitions atomically
        db.commit()
        db.refresh(exception)
        return exception
