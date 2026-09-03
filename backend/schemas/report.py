"""
ReconcileAI - Report & Summary Pydantic Schemas (Phase 14)
Defines response models for operational dashboard summary metrics.
"""

from typing import Dict
from pydantic import BaseModel, Field, ConfigDict

class OperationalSummaryResponse(BaseModel):
    """Real-time operational summary metrics derived from database state."""
    model_config = ConfigDict(extra="ignore")

    total_transactions: int
    total_reconciliation_results: int
    total_auto_reconciled: int
    total_exceptions: int
    open_exceptions: int
    approved_exceptions: int
    rejected_exceptions: int
    auto_reconciliation_rate: float
    unresolved_amount_inr: float
    exceptions_by_severity: Dict[str, int] = Field(default_factory=dict)
    exceptions_by_category: Dict[str, int] = Field(default_factory=dict)
    sla_status_breakdown: Dict[str, int] = Field(default_factory=dict)
