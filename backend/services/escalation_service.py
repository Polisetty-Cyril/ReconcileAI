"""
ReconcileAI - Operational Escalation Service (Phase 12C-1)
Implements deterministic escalation state machine for OPEN reconciliation exceptions:
Level 0 (Primary Reviewer) -> Level 1 (Finance Supervisor) -> Level 2 (Finance Director)

Safety Invariants:
- Operational monitoring only: never makes financial decisions or resolves exceptions.
- Evaluates only exceptions with status == 'OPEN'.
- Strictly monotonic forward transitions: 0 -> 1 and 1 -> 2 only.
- Modifies ONLY: escalation_level, escalated_at.
- Reuses SLAService from Phase 12B without duplicating SLA calculations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List
import logging
from sqlalchemy.orm import Session

from backend.models.exception import ReconciliationException
from backend.services.sla_service import SLAService

logger = logging.getLogger(__name__)


@dataclass
class EscalationResult:
    """Structured container for escalation evaluation output."""
    exception_id: str
    previous_level: int
    new_level: int
    transitioned: bool
    escalated_at: Optional[datetime]
    elapsed_ratio: float


class EscalationService:
    """
    Deterministic operational escalation service for ReconciliationException records.
    Consumes SLA metrics from SLAService and transitions escalation levels monotonically.
    """

    @staticmethod
    def get_target_escalation_level(elapsed_ratio: float) -> int:
        """
        Determines the target escalation level based on elapsed SLA ratio:
        - ratio < 1.0        -> Level 0 (Primary Reviewer)
        - 1.0 <= ratio < 2.0 -> Level 1 (Finance Supervisor)
        - ratio >= 2.0       -> Level 2 (Finance Director)
        """
        if elapsed_ratio < 1.0:
            return 0
        elif elapsed_ratio < 2.0:
            return 1
        else:
            return 2

    @classmethod
    def evaluate_exception(
        cls,
        exception: ReconciliationException,
        now: Optional[datetime] = None
    ) -> Optional[EscalationResult]:
        """
        Evaluates escalation for a single exception.

        Safety Rules:
        - Only processes exceptions with status == 'OPEN'.
        - Completely ignores and leaves untouched any non-OPEN exceptions (APPROVED, REJECTED, RESOLVED).
        - Modifies ONLY: escalation_level and escalated_at.
        - Escalation is strictly monotonic: 0 -> 1 or 1 -> 2. Never downgrades or exceeds 2.
        - Sets escalated_at only when an actual transition occurs.
        """
        if exception.status != "OPEN":
            logger.debug(
                "Skipping non-OPEN exception '%s' for escalation (status='%s')",
                exception.exception_id,
                exception.status
            )
            return None

        norm_now = SLAService.normalize_utc_datetime(now) if now is not None else datetime.now(timezone.utc)

        # Reuse SLAService to obtain the exact elapsed ratio without duplicating calculations
        _, _, ratio, _ = SLAService.calculate_sla_state(
            created_at=exception.created_at,
            severity=exception.severity,
            now=norm_now
        )

        target_level = cls.get_target_escalation_level(ratio)
        current_level = exception.escalation_level if exception.escalation_level is not None else 0

        # Monotonic forward transitions only: 0 -> 1, 1 -> 2, 0 -> 2
        transitioned = False
        new_level = current_level
        if current_level < target_level and target_level <= 2:
            new_level = target_level
            transitioned = True
            exception.escalation_level = new_level
            exception.escalated_at = norm_now

        return EscalationResult(
            exception_id=exception.exception_id,
            previous_level=current_level,
            new_level=new_level,
            transitioned=transitioned,
            escalated_at=exception.escalated_at,
            elapsed_ratio=round(ratio, 4)
        )

    @classmethod
    def evaluate_all_open_exceptions(
        cls,
        db: Session,
        now: Optional[datetime] = None
    ) -> List[EscalationResult]:
        """
        Evaluates all OPEN exceptions in the database for escalation,
        persists level transitions atomically, and returns structured results.
        """
        open_exceptions = db.query(ReconciliationException).filter(
            ReconciliationException.status == "OPEN"
        ).all()

        results: List[EscalationResult] = []
        any_transitioned = False

        for exc in open_exceptions:
            res = cls.evaluate_exception(exc, now=now)
            if res:
                results.append(res)
                if res.transitioned:
                    db.add(exc)
                    any_transitioned = True

        if any_transitioned:
            db.commit()

        return results
