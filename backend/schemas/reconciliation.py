"""
ReconcileAI - Reconciliation Pydantic Schemas (Phase 14)
Defines request and response schemas for reconciliation triggers and result listings.
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict

class ReconciliationResultDetailResponse(BaseModel):
    """Detailed view of a single ReconciliationResult record."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    reconciliation_id: str
    gateway_transaction_id: Optional[str] = None
    bank_transaction_id: Optional[str] = None
    erp_invoice_id: Optional[str] = None
    match_score: float
    matching_method: str
    ai_recommendation: Optional[str] = None
    ai_confidence: Optional[float] = None
    ai_reasoning: Optional[str] = None
    final_decision: str
    discrepancy_amount: float
    is_resolved: bool
    reconciled_at: datetime

class ReconciliationResultListResponse(BaseModel):
    """Paginated list of reconciliation results."""
    model_config = ConfigDict(extra="ignore")

    total: int
    limit: int
    offset: int
    items: List[ReconciliationResultDetailResponse] = Field(default_factory=list)

class ReconciliationRunResponse(BaseModel):
    """Summary outcome of executing the reconciliation pipeline."""
    model_config = ConfigDict(extra="ignore")

    status: str
    total_clusters: int
    total_reconciled: int
    total_review: int
    total_exceptions: int
    auto_reconciled_rate: float
    unresolved_value_at_risk: float
    message: str
