"""
ReconcileAI - Operational SLA Monitoring Service (Phase 12B)
Calculates severity-derived SLA deadlines, elapsed time ratios, and status thresholds
for OPEN reconciliation exceptions.

Safety Rules:
- Operational monitoring only: never makes autonomous financial decisions.
- Evaluates only exceptions with status == 'OPEN'.
- Completely ignores and preserves historical non-OPEN exceptions (APPROVED, REJECTED, RESOLVED).
- Updates ONLY: sla_duration_hours, sla_deadline, sla_status.
- Never modifies: status, resolved_by, resolved_at, reviewer_notes,
  escalation_level, escalated_at, is_resolved, final_decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict
import logging
from sqlalchemy.orm import Session

from backend.models.exception import ReconciliationException

logger = logging.getLogger(__name__)

# Single source of truth for severity SLA durations in hours
SLA_SEVERITY_DURATIONS: Dict[str, float] = {
    "CRITICAL": 1.0,
    "HIGH": 4.0,
    "MEDIUM": 24.0,
    "LOW": 48.0,
}

# Standard default duration for unrecognized or missing severity (matches ReconciliationException.severity default)
DEFAULT_SLA_DURATION_HOURS: float = 24.0

# Ratio thresholds for SLA statuses
SLA_RATIO_WARNING_THRESHOLD: float = 0.75
SLA_RATIO_BREACH_THRESHOLD: float = 1.0


@dataclass
class SLAEvaluationResult:
    """Structured container for SLA evaluation output."""
    exception_id: str
    severity: str
    sla_duration_hours: float
    sla_deadline: datetime
    elapsed_ratio: float
    sla_status: str


class SLAService:
    """
    Deterministic operational SLA evaluation service for ReconciliationException records.
    Provides duration lookups, timezone normalization, deadline calculations,
    and threshold evaluations.
    """

    @staticmethod
    def get_sla_duration(severity: Optional[str]) -> float:
        """
        Returns authoritative SLA duration in hours based on exception severity.
        Unrecognized or missing severities safely default to 24.0 hours (MEDIUM),
        consistent with the default severity in ReconciliationException.
        """
        if not severity:
            return DEFAULT_SLA_DURATION_HOURS
        clean_severity = severity.strip().upper()
        return SLA_SEVERITY_DURATIONS.get(clean_severity, DEFAULT_SLA_DURATION_HOURS)

    @staticmethod
    def normalize_utc_datetime(dt: Optional[datetime]) -> Optional[datetime]:
        """
        Ensures a datetime object is timezone-aware with UTC timezone.
        Handles SQLite naive datetimes by attaching UTC tzinfo.
        """
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @classmethod
    def calculate_sla_state(
        cls,
        created_at: datetime,
        severity: str,
        now: Optional[datetime] = None
    ) -> tuple[float, datetime, float, str]:
        """
        Calculates (sla_duration_hours, sla_deadline, elapsed_ratio, sla_status)
        given creation timestamp, severity, and reference time `now`.
        """
        norm_created_at = cls.normalize_utc_datetime(created_at)
        if norm_created_at is None:
            norm_created_at = datetime.now(timezone.utc)

        norm_now = cls.normalize_utc_datetime(now) if now is not None else datetime.now(timezone.utc)

        duration_hours = cls.get_sla_duration(severity)
        deadline = norm_created_at + timedelta(hours=duration_hours)

        total_seconds = duration_hours * 3600.0
        elapsed_seconds = (norm_now - norm_created_at).total_seconds()

        if elapsed_seconds <= 0:
            ratio = 0.0
        elif total_seconds <= 0:
            ratio = 1.0
        else:
            ratio = elapsed_seconds / total_seconds

        # Determine SLA status based on exact boundary conditions
        if ratio < SLA_RATIO_WARNING_THRESHOLD:
            status = "OK"
        elif ratio < SLA_RATIO_BREACH_THRESHOLD:
            status = "WARNING"
        else:
            status = "BREACHED"

        return duration_hours, deadline, ratio, status

    @classmethod
    def evaluate_exception(
        cls,
        exception: ReconciliationException,
        now: Optional[datetime] = None
    ) -> Optional[SLAEvaluationResult]:
        """
        Evaluates SLA for a single exception.
        
        SAFETY INVARIANTS:
        - Only processes exceptions with status == 'OPEN'.
        - Completely ignores and leaves untouched any non-OPEN exceptions (APPROVED, REJECTED, RESOLVED).
        - Updates ONLY: sla_duration_hours, sla_deadline, sla_status.
        - Does NOT update: status, resolved_by, resolved_at, reviewer_notes,
          escalation_level, escalated_at, is_resolved, final_decision.
        """
        if exception.status != "OPEN":
            logger.debug(
                "Skipping non-OPEN exception '%s' (status='%s')",
                exception.exception_id,
                exception.status
            )
            return None

        duration_hours, deadline, ratio, sla_status = cls.calculate_sla_state(
            created_at=exception.created_at,
            severity=exception.severity,
            now=now
        )

        # Update ONLY the 3 SLA fields
        exception.sla_duration_hours = duration_hours
        exception.sla_deadline = deadline
        exception.sla_status = sla_status

        return SLAEvaluationResult(
            exception_id=exception.exception_id,
            severity=exception.severity,
            sla_duration_hours=duration_hours,
            sla_deadline=deadline,
            elapsed_ratio=round(ratio, 4),
            sla_status=sla_status
        )

    @classmethod
    def evaluate_all_open_exceptions(
        cls,
        db: Session,
        now: Optional[datetime] = None
    ) -> List[SLAEvaluationResult]:
        """
        Evaluates all OPEN exceptions in the database, updates their SLA fields,
        and commits the changes atomically within the provided session.
        Returns a list of SLAEvaluationResult records.
        """
        open_exceptions = db.query(ReconciliationException).filter(
            ReconciliationException.status == "OPEN"
        ).all()

        results: List[SLAEvaluationResult] = []
        for exc in open_exceptions:
            eval_res = cls.evaluate_exception(exc, now=now)
            if eval_res:
                db.add(exc)
                results.append(eval_res)

        if results:
            db.commit()

        return results
