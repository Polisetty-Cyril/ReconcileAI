"""
ReconcileAI - Report & Summary Pydantic Schemas (Phase 14)
Defines response models for operational dashboard summary metrics.
"""

from typing import Dict, List, Optional
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
    decision_breakdown: Dict[str, int] = Field(default_factory=dict)


class ExecutiveReportResponse(BaseModel):
    """Extended executive financial reconciliation statement."""
    model_config = ConfigDict(extra="ignore")

    total_transactions: int
    total_transaction_value_inr: float
    total_reconciliation_results: int
    total_auto_reconciled: int
    auto_reconciliation_rate: float
    total_exceptions: int
    open_exceptions: int
    approved_exceptions: int
    rejected_exceptions: int
    unresolved_amount_inr: float
    exceptions_by_severity: Dict[str, int] = Field(default_factory=dict)
    exceptions_by_category: Dict[str, int] = Field(default_factory=dict)
    sla_status_breakdown: Dict[str, int] = Field(default_factory=dict)
    decision_breakdown: Dict[str, int] = Field(default_factory=dict)
    generated_at: str


class ReconciliationReportItem(BaseModel):
    """Granular multi-source reconciliation candidate cluster record."""
    model_config = ConfigDict(extra="ignore")

    reconciliation_id: str
    gateway_transaction_id: Optional[str] = None
    bank_transaction_id: Optional[str] = None
    erp_invoice_id: Optional[str] = None
    matching_method: str
    match_score: float
    discrepancy_amount: float
    ai_recommendation: Optional[str] = None
    ai_confidence: Optional[float] = None
    ai_reasoning: Optional[str] = None
    final_decision: str
    is_resolved: bool
    reconciled_at: Optional[str] = None


class ReconciliationReportResponse(BaseModel):
    """Response model for full reconciliation report datasets."""
    model_config = ConfigDict(extra="ignore")

    total: int
    items: List[ReconciliationReportItem] = Field(default_factory=list)


class ExceptionAgingReportItem(BaseModel):
    """Detailed discrepancy item with SLA and aging progression."""
    model_config = ConfigDict(extra="ignore")

    exception_id: str
    reconciliation_id: Optional[str] = None
    transaction_id: str
    category: str
    severity: str
    difference_amount: float
    status: str
    sla_duration_hours: float
    sla_deadline: Optional[str] = None
    sla_status: str
    escalation_level: int
    escalated_at: Optional[str] = None
    created_at: Optional[str] = None
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None
    reviewer_notes: Optional[str] = None


class ExceptionAgingReportResponse(BaseModel):
    """Response model for exception aging report dataset."""
    model_config = ConfigDict(extra="ignore")

    total: int
    items: List[ExceptionAgingReportItem] = Field(default_factory=list)


class AuditComplianceReportItem(BaseModel):
    """Read-only audit event record for regulatory reporting."""
    model_config = ConfigDict(extra="ignore")

    audit_id: str
    timestamp: str
    actor: str
    action: str
    entity: str
    entity_id: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    reason: Optional[str] = None


class AuditComplianceReportResponse(BaseModel):
    """Response model for regulatory audit compliance dataset."""
    model_config = ConfigDict(extra="ignore")

    total: int
    items: List[AuditComplianceReportItem] = Field(default_factory=list)
