"""
ReconcileAI - AuditLog SQLAlchemy Model
Provides an append-only, immutable audit trail of every financial action,
decision, webhook arrival, and manual reviewer override.
Enforces strict immutability: updates and deletes are prohibited at both
the ORM instance level and the Session/Query bulk level.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, event
from sqlalchemy.orm import Session

from backend.database import Base

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Immutability Exceptions & Scoped Test Cleanup Context
# ---------------------------------------------------------------------------

class AuditLogImmutableError(PermissionError):
    """
    Raised when an attempt is made to update or delete an immutable AuditLog record.
    Audit records are strictly append-only.
    """
    pass


_audit_log_cleanup_active: ContextVar[bool] = ContextVar(
    "_audit_log_cleanup_active", default=False
)


@contextmanager
def audit_log_cleanup_context():
    """
    Explicit, narrowly scoped context manager for test harness teardown/cleanup.
    Temporarily permits bulk deletion of test audit rows in isolated test environments.
    Updates remain strictly prohibited even within this context.
    Never active in production.
    """
    token = _audit_log_cleanup_active.set(True)
    try:
        yield
    finally:
        _audit_log_cleanup_active.reset(token)


def is_audit_log_cleanup_active() -> bool:
    """Returns True if the current execution context is within audit_log_cleanup_context."""
    return _audit_log_cleanup_active.get()


# ---------------------------------------------------------------------------
# AuditLog Model Definition
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# SQLAlchemy Immutability Enforcement Hooks
# ---------------------------------------------------------------------------

@event.listens_for(AuditLog, "before_update")
def _audit_log_before_update(mapper, connection, target):
    """
    Intercepts instance-level flush updates on AuditLog.
    Updates are unconditionally forbidden.
    """
    audit_id = getattr(target, "audit_id", "unknown")
    raise AuditLogImmutableError(
        f"AuditLog records are strictly immutable and cannot be updated. "
        f"Attempted mutation on audit_id='{audit_id}'."
    )


@event.listens_for(AuditLog, "before_delete")
def _audit_log_before_delete(mapper, connection, target):
    """
    Intercepts instance-level deletions (session.delete) on AuditLog.
    Forbidden in production; permitted only when audit_log_cleanup_context is active.
    """
    if not is_audit_log_cleanup_active():
        audit_id = getattr(target, "audit_id", "unknown")
        raise AuditLogImmutableError(
            f"AuditLog records are strictly immutable and cannot be deleted. "
            f"Attempted deletion of audit_id='{audit_id}'."
        )


@event.listens_for(Session, "do_orm_execute")
def _audit_log_do_orm_execute(orm_execute_state):
    """
    Intercepts statement-level ORM executions targeting AuditLog before SQL is emitted.
    Catches:
    - Query.update() and session.execute(update(AuditLog)...) -> Always forbidden.
    - Query.delete() and session.execute(delete(AuditLog)...) -> Forbidden unless cleanup context active.
    """
    if orm_execute_state.is_update:
        for mapper in orm_execute_state.all_mappers:
            if issubclass(mapper.class_, AuditLog):
                raise AuditLogImmutableError(
                    "Bulk UPDATE operations on AuditLog are strictly forbidden. "
                    "AuditLog records are append-only and immutable."
                )

    elif orm_execute_state.is_delete:
        for mapper in orm_execute_state.all_mappers:
            if issubclass(mapper.class_, AuditLog):
                if not is_audit_log_cleanup_active():
                    raise AuditLogImmutableError(
                        "Bulk DELETE operations on AuditLog are strictly forbidden. "
                        "AuditLog records are append-only and immutable."
                    )
