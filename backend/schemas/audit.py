"""
ReconcileAI - Audit Log Pydantic Schemas (Phase 14)
Defines read-only response models for audit trail queries.
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict

class AuditLogResponse(BaseModel):
    """Read-only view of an immutable AuditLog record."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    audit_id: str
    timestamp: datetime
    actor: str
    action: str
    entity: str
    entity_id: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    reason: Optional[str] = None

class AuditLogListResponse(BaseModel):
    """Paginated list of audit trail entries."""
    model_config = ConfigDict(extra="ignore")

    total: int
    limit: int
    offset: int
    items: List[AuditLogResponse] = Field(default_factory=list)
