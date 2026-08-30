"""
ReconcileAI - ReconciliationException SQLAlchemy Model
Manages financial discrepancies, missing records, amount mismatches, and reviewer lifecycle.
"""

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Index
from backend.database import Base

class ReconciliationException(Base):
    __tablename__ = "reconciliation_exceptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    exception_id = Column(String(100), unique=True, nullable=False, index=True)
    reconciliation_id = Column(String(100), nullable=True, index=True)
    transaction_id = Column(String(100), nullable=False, index=True)
    
    category = Column(String(50), nullable=False, index=True)
    # Categories:
    # AMOUNT_MISMATCH, MISSING_BANK_TRANSACTION, MISSING_GATEWAY_TRANSACTION,
    # DUPLICATE_TRANSACTION, DATE_MISMATCH, REFERENCE_MISMATCH, PARTIAL_PAYMENT,
    # FAILED_PAYMENT, UNEXPECTED_BANK_TRANSACTION, ANOMALY
    
    severity = Column(String(20), default="MEDIUM", nullable=False)  # LOW, MEDIUM, HIGH, CRITICAL
    difference_amount = Column(Float, default=0.0, nullable=False)
    ai_explanation = Column(Text, nullable=True)
    
    status = Column(String(50), default="OPEN", nullable=False, index=True)  # OPEN, APPROVED, REJECTED, RESOLVED
    reviewer_notes = Column(Text, nullable=True)
    resolved_by = Column(String(100), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("idx_exc_status_cat", "status", "category"),
    )

    def __repr__(self):
        return f"<ReconciliationException(id='{self.exception_id}', category='{self.category}', status='{self.status}')>"
