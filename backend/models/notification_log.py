"""
ReconcileAI - NotificationLog SQLAlchemy Model (Phase 12A)
Provides an append-only, deduplicated notification log with database-enforced idempotency.
"""

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, Text, Index
from backend.database import Base

class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    notification_id = Column(String(100), unique=True, nullable=False, index=True)
    exception_id = Column(String(100), nullable=False, index=True)
    event_type = Column(String(50), nullable=False, index=True)  # SLA_WARNING, SLA_BREACH, ESCALATION_L1, ESCALATION_L2
    
    recipient_role = Column(String(100), nullable=False)        # PRIMARY_REVIEWER, FINANCE_SUPERVISOR, FINANCE_DIRECTOR
    recipient_email = Column(String(200), nullable=False)
    subject = Column(String(500), nullable=False)
    body = Column(Text, nullable=False)
    
    # Database-level unique constraint for idempotency
    idempotency_key = Column(String(200), unique=True, nullable=False, index=True)
    status = Column(String(20), default="SENT", nullable=False) # SENT, FAILED
    sent_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("idx_notif_exc_event", "exception_id", "event_type"),
    )

    def __repr__(self):
        return (
            f"<NotificationLog(id='{self.notification_id}', "
            f"exception_id='{self.exception_id}', event='{self.event_type}', "
            f"idempotency_key='{self.idempotency_key}')>"
        )
