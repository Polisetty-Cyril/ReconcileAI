"""
ReconcileAI - AuditLog SQLAlchemy Model
Provides an append-only, immutable audit trail of every financial action,
decision, webhook arrival, and manual reviewer override.
"""

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, Text, Index
from backend.database import Base

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    audit_id = Column(String(100), unique=True, nullable=False, index=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    
    actor = Column(String(100), nullable=False)  # SYSTEM, AI_CONTROLLER, HUMAN_OPERATOR, WEBHOOK_GATEWAY
    action = Column(String(100), nullable=False, index=True)
    # Actions:
    # TRANSACTION_INGESTED, AUTO_RECONCILED, AI_REASONED, EXCEPTION_CREATED,
    # EXCEPTION_APPROVED, EXCEPTION_REJECTED, MANUAL_OVERRIDE, WEBHOOK_RECEIVED,
    # WEBHOOK_DUPLICATE_REJECTED, WEBHOOK_SIGNATURE_FAILED
    
    entity = Column(String(100), nullable=False)  # TRANSACTION, RECONCILIATION, EXCEPTION, WEBHOOK
    entity_id = Column(String(100), nullable=False, index=True)
    
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_audit_actor_action", "actor", "action"),
        Index("idx_audit_entity_entity_id", "entity", "entity_id"),
    )

    def __repr__(self):
        return f"<AuditLog(id='{self.audit_id}', actor='{self.actor}', action='{self.action}', entity_id='{self.entity_id}')>"
