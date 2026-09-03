"""
ReconcileAI - SLA & Escalation Orchestrator (Phase 12C-4)
Coordinates SLAService, EscalationService, NotificationService, and MockEmailTransport
for OPEN reconciliation exceptions.

Safety Rules:
- Coordinates existing services without duplicating SLA, escalation, or notification rules.
- Evaluates only exceptions with status == 'OPEN'.
- Preserves notification event order: SLA evaluation -> Escalation evaluation ->
  SLA notification -> Escalation notification -> Optional delivery.
- Does NOT send real email or make network calls.
- Does NOT automatically resolve exceptions or make financial decisions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy.orm import Session

from backend.models.exception import ReconciliationException
from backend.services.sla_service import SLAService
from backend.services.escalation_service import EscalationService
from backend.services.notification_service import NotificationService, NotificationResult
from backend.services.email_transport import MockEmailTransport
from backend.services.audit_service import AuditService

logger = logging.getLogger(__name__)


@dataclass
class OrchestrationNotificationDetail:
    """Detail of a notification processed during orchestration."""
    event_type: str
    notification_id: str
    idempotency_key: str
    created: bool
    existing: bool
    delivered: bool
    delivery_status: str


@dataclass
class OrchestrationResult:
    """Structured result of processing an exception through the SLA orchestrator."""
    exception_id: str
    sla_status: str
    sla_deadline: Optional[datetime]
    sla_duration_hours: float
    elapsed_ratio: float
    escalation_level: int
    escalation_changed: bool
    notification_created: bool = False
    notification_delivered: bool = False
    delivery_status: Optional[str] = None
    notifications: List[OrchestrationNotificationDetail] = field(default_factory=list)


class SLAOrchestrator:
    """
    Deterministic coordinator for SLA evaluation, escalation state transitions,
    and operational notification dispatch.
    """

    @classmethod
    def process_exception(
        cls,
        db: Session,
        exception: ReconciliationException,
        now: datetime,
        transport: Optional[MockEmailTransport] = None
    ) -> Optional[OrchestrationResult]:
        """
        Processes a single exception through the complete operational SLA lifecycle.

        Workflow order:
        1. Non-OPEN safety check: ignores non-OPEN exceptions immediately.
        2. SLA evaluation via SLAService.
        3. Escalation evaluation via EscalationService.
        4. Persist updated SLA and escalation fields on the exception.
        5. SLA notification:
           - WARNING -> SLA_WARNING (level 0)
           - BREACHED -> SLA_BREACH (level 1)
        6. Escalation notification (only on actual level transition):
           - 0 -> 1: ESCALATION_L1 (level 1)
           - 1 -> 2 or 0 -> 2: ESCALATION_L2 (level 2)
        7. Optional delivery via supplied MockEmailTransport (leaves PENDING if transport is None).
        """
        # Step 1: Open exceptions only
        if exception.status != "OPEN":
            logger.debug(
                "Orchestrator skipping non-OPEN exception '%s' (status='%s')",
                exception.exception_id,
                exception.status
            )
            return None

        norm_now = SLAService.normalize_utc_datetime(now)
        audit_service = AuditService(db=db)

        # Step 2: SLA evaluation via SLAService
        old_sla_status = exception.sla_status
        sla_eval = SLAService.evaluate_exception(exception, now=norm_now)
        if not sla_eval:
            return None

        # Audit meaningful SLA state transition (only once on actual transition)
        new_sla_status = exception.sla_status
        if new_sla_status != old_sla_status:
            if new_sla_status == "WARNING":
                audit_service.log_action(
                    actor="SYSTEM",
                    action="SLA_WARNING",
                    entity="EXCEPTION",
                    entity_id=exception.exception_id,
                    old_value=old_sla_status,
                    new_value="WARNING",
                    reason=f"SLA warning threshold reached (elapsed ratio: {sla_eval.elapsed_ratio:.2f})",
                    commit=False,
                )
            elif new_sla_status == "BREACHED":
                audit_service.log_action(
                    actor="SYSTEM",
                    action="SLA_BREACHED",
                    entity="EXCEPTION",
                    entity_id=exception.exception_id,
                    old_value=old_sla_status,
                    new_value="BREACHED",
                    reason=f"SLA breach occurred (elapsed ratio: {sla_eval.elapsed_ratio:.2f})",
                    commit=False,
                )

        # Step 3: Escalation evaluation via EscalationService
        esc_eval = EscalationService.evaluate_exception(exception, now=norm_now)
        escalation_changed = esc_eval.transitioned if esc_eval else False
        current_level = exception.escalation_level if exception.escalation_level is not None else 0

        # Audit meaningful Escalation transition (only once on actual transition)
        if escalation_changed and esc_eval:
            if esc_eval.new_level == 1:
                audit_service.log_action(
                    actor="SYSTEM",
                    action="ESCALATION_L1",
                    entity="EXCEPTION",
                    entity_id=exception.exception_id,
                    old_value=str(esc_eval.previous_level),
                    new_value="1",
                    reason=f"Escalated to Level 1 (Finance Supervisor).",
                    commit=False,
                )
            elif esc_eval.new_level == 2:
                audit_service.log_action(
                    actor="SYSTEM",
                    action="ESCALATION_L2",
                    entity="EXCEPTION",
                    entity_id=exception.exception_id,
                    old_value=str(esc_eval.previous_level),
                    new_value="2",
                    reason=f"Escalated to Level 2 (Finance Director).",
                    commit=False,
                )

        # Step 4: Persist SLA and escalation changes to DB
        db.add(exception)
        db.commit()
        db.refresh(exception)

        # Step 5 & 6: Determine events to generate in strict chronological order
        events_to_process: List[tuple[str, int]] = []

        # 5. SLA notification event
        if exception.sla_status == "WARNING":
            events_to_process.append(("SLA_WARNING", 0))
        elif exception.sla_status == "BREACHED":
            events_to_process.append(("SLA_BREACH", 1))

        # 6. Escalation notification event (only on actual transition)
        if escalation_changed and esc_eval:
            if esc_eval.new_level == 1:
                events_to_process.append(("ESCALATION_L1", 1))
            elif esc_eval.new_level == 2:
                events_to_process.append(("ESCALATION_L2", 2))

        # Step 7: Create and optionally deliver notifications
        processed_notifications: List[OrchestrationNotificationDetail] = []
        any_created = False
        any_delivered = False
        last_delivery_status: Optional[str] = None

        for event_type, target_level in events_to_process:
            notif_res = NotificationService.create_notification(
                db=db,
                exception=exception,
                event_type=event_type,
                escalation_level=target_level,
                now=norm_now
            )
            if not notif_res:
                continue

            delivered = False
            curr_status = notif_res.status

            if notif_res.created:
                any_created = True
                # Deliver newly created notification if transport supplied
                if transport is not None:
                    deliv_res = NotificationService.deliver_notification(
                        db=db,
                        notification_id=notif_res.notification_id,
                        transport=transport,
                        now=norm_now
                    )
                    delivered = deliv_res.delivery_success is True
                    curr_status = deliv_res.status
                    if delivered:
                        any_delivered = True

            last_delivery_status = curr_status

            processed_notifications.append(
                OrchestrationNotificationDetail(
                    event_type=event_type,
                    notification_id=notif_res.notification_id,
                    idempotency_key=notif_res.idempotency_key,
                    created=notif_res.created,
                    existing=notif_res.existing,
                    delivered=delivered,
                    delivery_status=curr_status
                )
            )

        return OrchestrationResult(
            exception_id=exception.exception_id,
            sla_status=exception.sla_status,
            sla_deadline=exception.sla_deadline,
            sla_duration_hours=exception.sla_duration_hours,
            elapsed_ratio=sla_eval.elapsed_ratio,
            escalation_level=current_level,
            escalation_changed=escalation_changed,
            notification_created=any_created,
            notification_delivered=any_delivered,
            delivery_status=last_delivery_status,
            notifications=processed_notifications
        )

    @classmethod
    def process_all_open_exceptions(
        cls,
        db: Session,
        now: datetime,
        transport: Optional[MockEmailTransport] = None
    ) -> List[OrchestrationResult]:
        """
        Evaluates all OPEN exceptions in the database through the orchestrator.
        """
        open_exceptions = db.query(ReconciliationException).filter(
            ReconciliationException.status == "OPEN"
        ).all()

        results: List[OrchestrationResult] = []
        for exc in open_exceptions:
            res = cls.process_exception(db, exc, now=now, transport=transport)
            if res:
                results.append(res)
        return results
