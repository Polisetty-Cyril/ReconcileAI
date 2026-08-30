"""
ReconcileAI - Transaction SQLAlchemy Model
Represents canonical ingested records from Payment Gateway, Bank Statements, and ERP Ledgers.
"""

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Index
from backend.database import Base

class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(String(100), nullable=False, index=True)
    source = Column(String(50), nullable=False, index=True)  # GATEWAY, BANK, ERP
    reference_id = Column(String(100), nullable=True, index=True)  # payment_id, UTR, invoice ref
    order_id = Column(String(100), nullable=True, index=True)
    customer_id = Column(String(100), nullable=True, index=True)
    amount = Column(Float, nullable=False)
    currency = Column(String(10), default="INR", nullable=False)
    transaction_date = Column(DateTime, nullable=False, index=True)
    status = Column(String(50), nullable=False)  # captured, failed, PAID, CREDIT, etc.
    transaction_type = Column(String(50), nullable=False)  # PAYMENT, SETTLEMENT, INVOICE, REFUND
    description = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)  # Extended attributes (fees, taxes, customer names)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("idx_txn_source_ref", "source", "reference_id"),
        Index("idx_txn_date_amount", "transaction_date", "amount"),
    )

    def __repr__(self):
        return f"<Transaction(id={self.id}, source='{self.source}', txn_id='{self.transaction_id}', amount={self.amount})>"
