"""
ReconcileAI - Notification Service (Phase 12C-2)
Provides deterministic operational notification generation and database-backed
idempotency tracking for SLA and escalation events.

Safety Rules:
- Operational alerting only: notifications never modify financial state.
- Strictly idempotent via database UNIQUE(idempotency_key) constraint.
- Creates queued/pending NotificationLog records without actual delivery (no SMTP/transport).
- Evaluates only exceptions with status == 'OPEN'.
- Never modifies exception fields (status, is_resolved, final_decision, escalation_level, etc.).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from backend.models.exception import ReconciliationException
from backend.models.notification_log import NotificationLog
from backend.services.email_transport import MockEmailTransport

logger = logging.getLogger(__name__)

# Supported event types
SUPPORTED_EVENT_TYPES = (
    "SLA_WARNING",
    "SLA_BREACH",
    "ESCALATION_L1",
    "ESCALATION_L2",
)

# Centralized recipient role and email mapping per event type
EVENT_RECIPIENT_MAPPING: Dict[str, Dict[str, Any]] = {
    "SLA_WARNING": {
        "role": "PRIMARY_REVIEWER",
        "email": "reviewer@reconcileai.local",
        "escalation_level": 0,
    },
    "SLA_BREACH": {
        "role": "FINANCE_SUPERVISOR",
        "email": "supervisor@reconcileai.local",
        "escalation_level": 1,
    },
    "ESCALATION_L1": {
        "role": "FINANCE_SUPERVISOR",
        "email": "supervisor@reconcileai.local",
        "escalation_level": 1,
    },
    "ESCALATION_L2": {
        "role": "FINANCE_DIRECTOR",
        "email": "director@reconcileai.local",
        "escalation_level": 2,
    },
}

DEFAULT_NOTIFICATION_STATUS = "PENDING"


@dataclass
class NotificationResult:
    """Structured result of a notification creation or delivery attempt."""
    notification_id: str
    exception_id: str
    event_type: str
    idempotency_key: str
    recipient_role: str
    recipient_email: str
    status: str
    created: bool
    existing: bool
    sent_at: Optional[datetime] = None
    delivery_success: Optional[bool] = None
    delivery_error: Optional[str] = None



class NotificationService:
    """
    Deterministic service for generating operational SLA and escalation notifications
    with database-backed idempotency.
    """

    @staticmethod
    def generate_idempotency_key(
        exception_id: str,
        event_type: str,
        escalation_level: int
    ) -> str:
        """
        Constructs the authoritative composite idempotency key:
        {exception_id}:{event_type}:{escalation_level}
        """
        return f"{exception_id}:{event_type}:{escalation_level}"

    @classmethod
    def format_notification(
        cls,
        exception: ReconciliationException,
        event_type: str,
        escalation_level: int
    ) -> tuple[str, str]:
        """
        Constructs deterministic subject and body for operational notification.
        Contains exception ID, category, severity, difference amount, SLA status,
        SLA deadline, escalation level, and event type.
        Excludes passwords, credentials, secrets, or financial conclusions.
        """
        mapping = EVENT_RECIPIENT_MAPPING.get(event_type, {})
        role = mapping.get("role", "UNKNOWN_ROLE")

        deadline_str = (
            exception.sla_deadline.isoformat()
            if getattr(exception, "sla_deadline", None)
            else "NOT_SET"
        )
        diff_amount = getattr(exception, "difference_amount", 0.0)

        subject = f"[{event_type}] Exception {exception.exception_id} - Severity: {exception.severity}"

        body = (
            f"=== ReconcileAI Operational Alert ===\n"
            f"Event Type: {event_type}\n"
            f"Exception ID: {exception.exception_id}\n"
            f"Category: {exception.category}\n"
            f"Severity: {exception.severity}\n"
            f"Discrepancy Amount: ₹{diff_amount:,.2f}\n"
            f"Current SLA Status: {exception.sla_status}\n"
            f"SLA Deadline: {deadline_str}\n"
            f"Escalation Level: {escalation_level} ({role})\n"
            f"Action Required: Human review required via Exception Management queue.\n"
            f"======================================"
        )
        return subject, body

    @classmethod
    def create_notification(
        cls,
        db: Session,
        exception: ReconciliationException,
        event_type: str,
        escalation_level: Optional[int] = None,
        now: Optional[datetime] = None
    ) -> Optional[NotificationResult]:
        """
        Creates a NotificationLog entry with database-backed idempotency.
        
        Safety & Validation Rules:
        - Requires exception.status == 'OPEN'. Rejects/ignores non-OPEN exceptions.
        - Requires event_type in SUPPORTED_EVENT_TYPES.
        - Generates deterministic idempotency_key.
        - If an existing NotificationLog with the same key exists, returns it (created=False, existing=True).
        - If not, safely inserts a new NotificationLog with status='PENDING'.
        - Recovers from concurrent insert race conditions using savepoint without corrupting session.
        - NEVER modifies any field on the exception object.
        - Does NOT perform actual email or network delivery.
        """
        # Validate OPEN exception status
        if exception.status != "OPEN":
            logger.debug(
                "Skipping notification for non-OPEN exception '%s' (status='%s')",
                exception.exception_id,
                exception.status
            )
            return None

        # Validate event type
        if event_type not in SUPPORTED_EVENT_TYPES:
            raise ValueError(
                f"Unsupported notification event_type '{event_type}'. "
                f"Must be one of: {SUPPORTED_EVENT_TYPES}"
            )

        mapping = EVENT_RECIPIENT_MAPPING[event_type]
        resolved_level = escalation_level if escalation_level is not None else mapping["escalation_level"]
        role = mapping["role"]
        email = mapping["email"]

        idempotency_key = cls.generate_idempotency_key(
            exception.exception_id,
            event_type,
            resolved_level
        )

        # 1. Check for existing notification log
        existing_log = db.query(NotificationLog).filter_by(idempotency_key=idempotency_key).first()
        if existing_log is not None:
            logger.debug(
                "Notification with key '%s' already exists (id=%s). Returning existing.",
                idempotency_key,
                existing_log.notification_id
            )
            return NotificationResult(
                notification_id=existing_log.notification_id,
                exception_id=existing_log.exception_id,
                event_type=existing_log.event_type,
                idempotency_key=existing_log.idempotency_key,
                recipient_role=existing_log.recipient_role,
                recipient_email=existing_log.recipient_email,
                status=existing_log.status,
                created=False,
                existing=True
            )

        # 2. Construct notification content
        subject, body = cls.format_notification(exception, event_type, resolved_level)
        sent_timestamp = now if now is not None else datetime.now(timezone.utc)
        notification_id = f"NOTIF_{uuid.uuid4().hex[:12].upper()}"

        new_log = NotificationLog(
            notification_id=notification_id,
            exception_id=exception.exception_id,
            event_type=event_type,
            recipient_role=role,
            recipient_email=email,
            subject=subject,
            body=body,
            idempotency_key=idempotency_key,
            status=DEFAULT_NOTIFICATION_STATUS,
            sent_at=sent_timestamp
        )

        # 3. Attempt insertion with transaction safety
        try:
            with db.begin_nested():
                db.add(new_log)
                db.flush()
            db.commit()
            db.refresh(new_log)
            return NotificationResult(
                notification_id=new_log.notification_id,
                exception_id=new_log.exception_id,
                event_type=new_log.event_type,
                idempotency_key=new_log.idempotency_key,
                recipient_role=new_log.recipient_role,
                recipient_email=new_log.recipient_email,
                status=new_log.status,
                created=True,
                existing=False
            )
        except IntegrityError:
            # Check if this error was specifically due to an existing idempotency_key
            existing_after_race = db.query(NotificationLog).filter_by(idempotency_key=idempotency_key).first()
            if existing_after_race is not None:
                logger.debug(
                    "Recovered from duplicate idempotency_key race for key '%s'.",
                    idempotency_key
                )
                return NotificationResult(
                    notification_id=existing_after_race.notification_id,
                    exception_id=existing_after_race.exception_id,
                    event_type=existing_after_race.event_type,
                    idempotency_key=existing_after_race.idempotency_key,
                    recipient_role=existing_after_race.recipient_role,
                    recipient_email=existing_after_race.recipient_email,
                    status=existing_after_race.status,
                    created=False,
                    existing=True
                )
            # Not an idempotency key uniqueness race; re-raise!
            raise

    @classmethod
    def deliver_notification(
        cls,
        db: Session,
        notification_id: str,
        transport: MockEmailTransport,
        now: Optional[datetime] = None
    ) -> NotificationResult:
        """
        Delivers a PENDING notification using the provided mock email transport.

        Lifecycle Rules:
        - Must exist; raises ValueError if not found.
        - Must be PENDING; raises ValueError if already SENT, FAILED, or invalid.
        - Passes recipient_email, subject, body to transport.send.
        - On success: status becomes SENT, sent_at is updated with delivery time, commits.
        - On failure: status becomes FAILED, sent_at is NOT set, commits.
        - Never modifies financial exception records.
        - Preserves idempotency and existing notification_id.
        """
        notif = db.query(NotificationLog).filter_by(notification_id=notification_id).first()
        if not notif:
            raise ValueError(f"Notification with ID '{notification_id}' not found.")

        if notif.status != "PENDING":
            raise ValueError(
                f"Cannot deliver notification '{notification_id}' with status '{notif.status}'. "
                f"Only notifications in 'PENDING' status can be delivered."
            )

        send_result = transport.send(
            recipient_email=notif.recipient_email,
            subject=notif.subject,
            body=notif.body
        )

        delivery_timestamp = now if now is not None else datetime.now(timezone.utc)

        if send_result.success:
            notif.status = "SENT"
            notif.sent_at = delivery_timestamp
            db.add(notif)
            db.commit()
            db.refresh(notif)
            return NotificationResult(
                notification_id=notif.notification_id,
                exception_id=notif.exception_id,
                event_type=notif.event_type,
                idempotency_key=notif.idempotency_key,
                recipient_role=notif.recipient_role,
                recipient_email=notif.recipient_email,
                status=notif.status,
                created=False,
                existing=True,
                sent_at=notif.sent_at,
                delivery_success=True,
                delivery_error=None
            )
        else:
            notif.status = "FAILED"
            # On failed mock delivery: do NOT set sent_at
            db.add(notif)
            db.commit()
            db.refresh(notif)
            return NotificationResult(
                notification_id=notif.notification_id,
                exception_id=notif.exception_id,
                event_type=notif.event_type,
                idempotency_key=notif.idempotency_key,
                recipient_role=notif.recipient_role,
                recipient_email=notif.recipient_email,
                status=notif.status,
                created=False,
                existing=True,
                sent_at=None,
                delivery_success=False,
                delivery_error=send_result.error
            )

