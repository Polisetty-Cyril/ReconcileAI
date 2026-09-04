"""
ReconcileAI - Reporting & Analytics Service (Phase 16)
Provides decoupled, read-only aggregation and data extraction for executive statements,
three-leg reconciliation reports, discrepancy/SLA aging analysis, and regulatory audit compliance.
"""

from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.models.transaction import Transaction
from backend.models.reconciliation import ReconciliationResult
from backend.models.exception import ReconciliationException
from backend.models.audit import AuditLog


class ReportingService:
    """
    Centralized, pure read-only service assembling operational, executive,
    and compliance datasets for reporting and export.
    Contains zero mutating database operations.
    """

    @staticmethod
    def get_operational_summary(db: Session) -> Dict[str, Any]:
        """
        Calculates real-time operational summary metrics from database state.
        Preserves complete backward compatibility with Phase 14 /reports/summary.
        """
        total_txns = db.query(Transaction).count()
        all_results = db.query(ReconciliationResult).all()
        total_results = len(all_results)
        auto_reconciled = sum(1 for r in all_results if r.final_decision == "AUTO_RECONCILED")

        all_exceptions = db.query(ReconciliationException).all()
        total_exceptions = len(all_exceptions)
        open_exceptions = [e for e in all_exceptions if e.status == "OPEN"]
        approved_exceptions = sum(1 for e in all_exceptions if e.status == "APPROVED")
        rejected_exceptions = sum(1 for e in all_exceptions if e.status == "REJECTED")

        auto_rate = round(auto_reconciled / total_results * 100, 2) if total_results else 0.0
        var_amount = round(sum(e.difference_amount for e in open_exceptions), 2)

        # Severity breakdown
        severity_counts: Dict[str, int] = {}
        for e in all_exceptions:
            severity_counts[e.severity] = severity_counts.get(e.severity, 0) + 1

        # Category breakdown
        category_counts: Dict[str, int] = {}
        for e in all_exceptions:
            category_counts[e.category] = category_counts.get(e.category, 0) + 1

        # SLA status breakdown
        sla_counts: Dict[str, int] = {}
        for e in all_exceptions:
            status_val = getattr(e, "sla_status", "OK")
            sla_counts[status_val] = sla_counts.get(status_val, 0) + 1

        # Decision breakdown
        decision_counts: Dict[str, int] = {}
        for r in all_results:
            decision_counts[r.final_decision] = decision_counts.get(r.final_decision, 0) + 1

        return {
            "total_transactions": total_txns,
            "total_reconciliation_results": total_results,
            "total_auto_reconciled": auto_reconciled,
            "total_exceptions": total_exceptions,
            "open_exceptions": len(open_exceptions),
            "approved_exceptions": approved_exceptions,
            "rejected_exceptions": rejected_exceptions,
            "auto_reconciliation_rate": auto_rate,
            "unresolved_amount_inr": var_amount,
            "exceptions_by_severity": severity_counts,
            "exceptions_by_category": category_counts,
            "sla_status_breakdown": sla_counts,
            "decision_breakdown": decision_counts
        }

    @staticmethod
    def get_executive_report(db: Session) -> Dict[str, Any]:
        """
        Generates extended financial executive report including total transaction value.
        """
        summary = ReportingService.get_operational_summary(db)

        # Calculate total transaction volume (INR) across canonical transactions
        total_val_row = db.query(func.sum(Transaction.amount)).scalar()
        total_val_inr = round(float(total_val_row or 0.0), 2)

        summary["total_transaction_value_inr"] = total_val_inr
        summary["generated_at"] = datetime.now(timezone.utc).isoformat()
        return summary

    @staticmethod
    def get_reconciliation_report(
        db: Session,
        final_decision: Optional[str] = None,
        is_resolved: Optional[bool] = None,
        reconciliation_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Extracts multi-source candidate cluster reconciliation records for three-leg reporting.
        """
        query = db.query(ReconciliationResult)
        if final_decision and final_decision.strip() != "ALL":
            query = query.filter(ReconciliationResult.final_decision == final_decision.strip())
        if is_resolved is not None:
            query = query.filter(ReconciliationResult.is_resolved == is_resolved)
        if reconciliation_id and reconciliation_id.strip():
            query = query.filter(ReconciliationResult.reconciliation_id == reconciliation_id.strip())

        results = query.order_by(ReconciliationResult.reconciled_at.desc(), ReconciliationResult.id.desc()).all()

        items = []
        for r in results:
            items.append({
                "reconciliation_id": r.reconciliation_id,
                "gateway_transaction_id": r.gateway_transaction_id,
                "bank_transaction_id": r.bank_transaction_id,
                "erp_invoice_id": r.erp_invoice_id,
                "matching_method": r.matching_method,
                "match_score": float(r.match_score),
                "discrepancy_amount": float(r.discrepancy_amount),
                "ai_recommendation": r.ai_recommendation,
                "ai_confidence": float(r.ai_confidence) if r.ai_confidence is not None else None,
                "ai_reasoning": r.ai_reasoning,
                "final_decision": r.final_decision,
                "is_resolved": bool(r.is_resolved),
                "reconciled_at": r.reconciled_at.isoformat() if r.reconciled_at else None
            })
        return items

    @staticmethod
    def get_exception_aging_report(
        db: Session,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        category: Optional[str] = None,
        sla_status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Extracts detailed discrepancy records with SLA urgency and escalation progression.
        """
        query = db.query(ReconciliationException)
        if status and status.strip() != "ALL":
            query = query.filter(ReconciliationException.status == status.strip().upper())
        if severity and severity.strip() != "ALL":
            query = query.filter(ReconciliationException.severity == severity.strip().upper())
        if category and category.strip() != "ALL":
            query = query.filter(ReconciliationException.category == category.strip())
        if sla_status and sla_status.strip() != "ALL":
            query = query.filter(ReconciliationException.sla_status == sla_status.strip().upper())

        exceptions = query.all()

        # Urgency triage sort: Breached -> Warning -> OK, then Critical -> High -> Medium -> Low
        sla_rank = {"BREACHED": 0, "WARNING": 1, "OK": 2}
        sev_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

        sorted_exceptions = sorted(
            exceptions,
            key=lambda x: (
                sla_rank.get(getattr(x, "sla_status", "OK"), 3),
                sev_rank.get(getattr(x, "severity", "MEDIUM"), 4),
                -getattr(x, "escalation_level", 0),
                -abs(float(x.difference_amount or 0.0))
            )
        )

        items = []
        for e in sorted_exceptions:
            items.append({
                "exception_id": e.exception_id,
                "reconciliation_id": e.reconciliation_id,
                "transaction_id": e.transaction_id,
                "category": e.category,
                "severity": e.severity,
                "difference_amount": float(e.difference_amount or 0.0),
                "status": e.status,
                "sla_duration_hours": float(getattr(e, "sla_duration_hours", 24.0)),
                "sla_deadline": e.sla_deadline.isoformat() if getattr(e, "sla_deadline", None) else None,
                "sla_status": getattr(e, "sla_status", "OK"),
                "escalation_level": int(getattr(e, "escalation_level", 0)),
                "escalated_at": e.escalated_at.isoformat() if getattr(e, "escalated_at", None) else None,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
                "resolved_by": e.resolved_by,
                "reviewer_notes": e.reviewer_notes
            })
        return items

    @staticmethod
    def get_audit_compliance_report(
        db: Session,
        entity: Optional[str] = None,
        entity_id: Optional[str] = None,
        action: Optional[str] = None,
        actor: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Extracts immutable audit trail entries for regulatory compliance export.
        Strictly read-only; never mutates audit records.
        """
        query = db.query(AuditLog)
        if entity and entity.strip() != "ALL":
            query = query.filter(AuditLog.entity == entity.strip().upper())
        if entity_id and entity_id.strip():
            query = query.filter(AuditLog.entity_id == entity_id.strip())
        if action and action.strip() != "ALL":
            query = query.filter(AuditLog.action == action.strip().upper())
        if actor and actor.strip() != "ALL":
            query = query.filter(AuditLog.actor == actor.strip())

        logs = query.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).all()

        items = []
        for log in logs:
            items.append({
                "audit_id": log.audit_id,
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "actor": log.actor,
                "action": log.action,
                "entity": log.entity,
                "entity_id": log.entity_id,
                "old_value": log.old_value,
                "new_value": log.new_value,
                "reason": log.reason
            })
        return items

    @staticmethod
    def get_all_transactions(
        db: Session,
        source: Optional[str] = None,
        status_filter: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieves all canonical transactions matching filters without pagination cap for complete export.
        """
        query = db.query(Transaction)
        if source and source.strip() != "ALL":
            query = query.filter(Transaction.source == source.strip().upper())
        if status_filter and status_filter.strip() != "ALL":
            query = query.filter(Transaction.status == status_filter.strip().upper())
        if start_date:
            query = query.filter(Transaction.transaction_date >= start_date)
        if end_date:
            query = query.filter(Transaction.transaction_date <= end_date)

        txns = query.order_by(Transaction.transaction_date.desc(), Transaction.id.desc()).all()

        items = []
        for t in txns:
            items.append({
                "transaction_id": t.transaction_id,
                "source": t.source,
                "reference_id": t.reference_id,
                "order_id": t.order_id,
                "customer_id": t.customer_id,
                "amount": float(t.amount),
                "currency": t.currency,
                "status": t.status,
                "transaction_type": t.transaction_type,
                "transaction_date": t.transaction_date.isoformat() if t.transaction_date else None,
                "description": t.description,
                "created_at": t.created_at.isoformat() if t.created_at else None
            })
        return items
