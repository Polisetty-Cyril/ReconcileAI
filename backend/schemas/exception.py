"""
ReconcileAI - Exception Management Pydantic Schemas (Phase 11)
Defines request and response models for the human exception management workflow.
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict

class ExceptionActionRequest(BaseModel):
    """
    Request body payload for approving or rejecting an exception.
    """
    model_config = ConfigDict(extra="ignore")

    reviewer_id: str = Field("HUMAN_OPERATOR", min_length=1, description="Identifier of the human reviewer")
    notes: Optional[str] = Field(None, description="Reviewer commentary, rationale, or resolution notes")

class ExceptionDetailResponse(BaseModel):
    """
    Detailed representation of a single reconciliation exception.
    """
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Internal database ID")
    exception_id: str = Field(..., description="Unique business identifier for the exception")
    reconciliation_id: Optional[str] = Field(None, description="Linked reconciliation result identifier")
    transaction_id: str = Field(..., description="Associated transaction or document identifier")
    category: str = Field(..., description="Exception discrepancy category (e.g. AMOUNT_MISMATCH, MISSING_BANK_TRANSACTION)")
    severity: str = Field(..., description="Severity level: LOW, MEDIUM, HIGH, CRITICAL")
    difference_amount: float = Field(0.0, description="Financial discrepancy amount in INR")
    ai_explanation: Optional[str] = Field(None, description="Advisory reasoning from Phase 8 AI Finance Controller")
    status: str = Field(..., description="Lifecycle status: OPEN, APPROVED, REJECTED, RESOLVED")
    reviewer_notes: Optional[str] = Field(None, description="Notes and commentary recorded by the human reviewer")
    resolved_by: Optional[str] = Field(None, description="Identifier of the human reviewer who resolved the exception")
    resolved_at: Optional[datetime] = Field(None, description="Timestamp when the exception was resolved")
    created_at: datetime = Field(..., description="Timestamp when the exception was created")

    # Phase 12A / Phase 15 — SLA Monitoring & Escalation Fields
    sla_duration_hours: Optional[float] = Field(24.0, description="Agreed SLA window in hours")
    sla_deadline: Optional[datetime] = Field(None, description="Timestamp by which exception must be addressed")
    sla_status: Optional[str] = Field("OK", description="Current SLA status: OK, WARNING, BREACHED")
    escalation_level: Optional[int] = Field(0, description="Escalation hierarchy level (0=Primary, 1=Supervisor, 2=Director)")
    escalated_at: Optional[datetime] = Field(None, description="Timestamp when escalation occurred")

class ExceptionListResponse(BaseModel):
    """
    Paginated list response for querying reconciliation exceptions.
    """
    model_config = ConfigDict(extra="ignore")

    total: int = Field(..., description="Total count of exceptions matching the query criteria")
    limit: int = Field(..., description="Pagination limit")
    offset: int = Field(..., description="Pagination offset")
    items: List[ExceptionDetailResponse] = Field(default_factory=list, description="List of exception records")
