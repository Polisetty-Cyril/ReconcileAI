"""
ReconcileAI - WebhookEvent SQLAlchemy Model
Tracks incoming payment webhooks, signature metadata, and idempotency status.
"""

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text
from backend.database import Base

class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(100), unique=True, nullable=False, index=True)  # Idempotency Key
    event_type = Column(String(100), nullable=False, index=True)  # e.g., payment.captured, payment.failed
    payment_id = Column(String(100), nullable=False, index=True)
    order_id = Column(String(100), nullable=True, index=True)
    amount = Column(Float, nullable=False)
    currency = Column(String(10), default="INR", nullable=False)
    signature = Column(String(255), nullable=True)
    payload_json = Column(Text, nullable=False)
    is_processed = Column(Boolean, default=False, nullable=False)
    received_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    def __repr__(self):
        return f"<WebhookEvent(event_id='{self.event_id}', event_type='{self.event_type}', payment_id='{self.payment_id}')>"
