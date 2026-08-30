"""
ReconcileAI - ReconciliationResult SQLAlchemy Model
Stores multi-source matching decisions, confidence scores, AI reasoning, and final status.
"""

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, Index
from backend.database import Base

class ReconciliationResult(Base):
    __tablename__ = "reconciliation_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    reconciliation_id = Column(String(100), unique=True, nullable=False, index=True)
    gateway_transaction_id = Column(String(100), nullable=True, index=True)
    bank_transaction_id = Column(String(100), nullable=True, index=True)
    erp_invoice_id = Column(String(100), nullable=True, index=True)
    
    match_score = Column(Float, nullable=False)  # 0 to 100
    matching_method = Column(String(50), nullable=False)  # EXACT_RULE, FUZZY_MATCH, AI_REASONING, MANUAL
    
    ai_recommendation = Column(String(50), nullable=True)  # AUTO_RECONCILE, REVIEW, EXCEPTION, NO_MATCH
    ai_confidence = Column(Float, nullable=True)  # 0 to 100
    ai_reasoning = Column(Text, nullable=True)
    
    final_decision = Column(String(50), nullable=False)  # AUTO_RECONCILED, AI_ASSISTED_RESOLVED, HUMAN_REVIEW, EXCEPTION
    discrepancy_amount = Column(Float, default=0.0, nullable=False)
    is_resolved = Column(Boolean, default=False, nullable=False)
    reconciled_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("idx_recon_decision_score", "final_decision", "match_score"),
    )

    def __repr__(self):
        return f"<ReconciliationResult(recon_id='{self.reconciliation_id}', decision='{self.final_decision}', score={self.match_score})>"
