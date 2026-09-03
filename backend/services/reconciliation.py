"""
ReconcileAI - Deterministic Rule-Based Reconciliation Engine (Phase 6)
Performs deterministic three-way matching across Payment Gateway, Bank Statement, and ERP Ledger.
Evaluates exact reference IDs, order IDs, amounts, date tolerances, and transaction statuses.
Emits clear deterministic reason codes and persists outcomes into reconciliation_results and reconciliation_exceptions.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple, Union
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models.transaction import Transaction
from backend.models.reconciliation import ReconciliationResult
from backend.models.exception import ReconciliationException
from backend.schemas.transaction import CanonicalTransaction
from backend.services.audit_service import AuditService

class ReconciliationReasonCode:
    """Standardized deterministic reason codes for explainability."""
    EXACT_MATCH = "EXACT_MATCH | REFERENCE_MATCH | AMOUNT_MATCH | DATE_WITHIN_TOLERANCE | STATUS_VALID"
    ORDER_MATCH = "ORDER_ID_MATCH | AMOUNT_MATCH | DATE_WITHIN_TOLERANCE | STATUS_VALID"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    DATE_MISMATCH = "DATE_MISMATCH | DATE_OUTSIDE_TOLERANCE"
    REFERENCE_MISMATCH = "REFERENCE_MISMATCH | ORDER_MATCHED_BUT_REF_DIFFERS"
    MISSING_BANK = "MISSING_BANK_TRANSACTION | GATEWAY_CAPTURED_WITHOUT_BANK_DEPOSIT"
    MISSING_GATEWAY = "MISSING_GATEWAY_TRANSACTION | BANK_DEPOSIT_WITHOUT_GATEWAY_RECORD"
    DUPLICATE_TRANSACTION = "DUPLICATE_TRANSACTION | MULTIPLE_RECORDS_FOR_REFERENCE"
    FAILED_PAYMENT = "FAILED_PAYMENT | GATEWAY_FAILED_NO_SETTLEMENT_EXPECTED"
    PARTIAL_PAYMENT = "PARTIAL_PAYMENT | RECEIVED_AMOUNT_LESS_THAN_INVOICE"
    STATUS_MISMATCH = "STATUS_MISMATCH | INVALID_TRANSACTION_STATUS"

class DeterministicReconciliationEngine:
    """
    Deterministic rule-based reconciliation engine.
    Compares normalized financial records across sources using strict rules.
    100% deterministic, reproducible, and explainable.
    """

    def __init__(
        self,
        amount_tolerance: float = settings.AMOUNT_TOLERANCE_INR,
        date_tolerance_days: int = settings.DATE_TOLERANCE_DAYS,
        exact_match_threshold: float = settings.EXACT_MATCH_SCORE_THRESHOLD
    ):
        self.amount_tolerance = amount_tolerance
        self.date_tolerance_days = date_tolerance_days
        self.exact_match_threshold = exact_match_threshold

    def generate_candidate_clusters(
        self,
        transactions: List[Union[Transaction, CanonicalTransaction]]
    ) -> List[Dict[str, List[Any]]]:
        """
        Groups multi-source transactions into candidate matching clusters using
        deterministic identifiers: reference_id, order_id, or transaction_id.
        """
        ref_to_txns: Dict[str, List[Any]] = {}
        order_to_txns: Dict[str, List[Any]] = {}
        unmatched_txns: List[Any] = []

        # Index transactions by primary reference and secondary order_id
        for txn in transactions:
            ref = getattr(txn, "reference_id", None)
            order = getattr(txn, "order_id", None)

            if ref:
                ref_to_txns.setdefault(ref, []).append(txn)
            if order:
                order_to_txns.setdefault(order, []).append(txn)
            if not ref and not order:
                unmatched_txns.append(txn)

        # Build candidate clusters
        processed_txn_ids = set()
        clusters: List[Dict[str, List[Any]]] = []

        # 1. Cluster by reference_id
        for ref, txn_list in ref_to_txns.items():
            cluster = {"GATEWAY": [], "BANK": [], "ERP": []}
            for t in txn_list:
                t_id = f"{t.source}_{t.transaction_id}"
                if t_id not in processed_txn_ids:
                    cluster[t.source.upper()].append(t)
                    processed_txn_ids.add(t_id)

            # Check if any associated order_id can pull in ERP/Gateway records that missed direct ref
            for t in list(cluster["GATEWAY"]) + list(cluster["BANK"]) + list(cluster["ERP"]):
                t_order = getattr(t, "order_id", None)
                if t_order and t_order in order_to_txns:
                    for ot in order_to_txns[t_order]:
                        ot_id = f"{ot.source}_{ot.transaction_id}"
                        if ot_id not in processed_txn_ids:
                            cluster[ot.source.upper()].append(ot)
                            processed_txn_ids.add(ot_id)

            if any(cluster.values()):
                clusters.append(cluster)

        # 2. Cluster remaining by order_id
        for order, txn_list in order_to_txns.items():
            cluster = {"GATEWAY": [], "BANK": [], "ERP": []}
            for t in txn_list:
                t_id = f"{t.source}_{t.transaction_id}"
                if t_id not in processed_txn_ids:
                    cluster[t.source.upper()].append(t)
                    processed_txn_ids.add(t_id)
            if any(cluster.values()):
                clusters.append(cluster)

        # 3. Unmatched single-transaction clusters
        for t in unmatched_txns:
            t_id = f"{t.source}_{t.transaction_id}"
            if t_id not in processed_txn_ids:
                cluster = {"GATEWAY": [], "BANK": [], "ERP": []}
                cluster[t.source.upper()].append(t)
                processed_txn_ids.add(t_id)
                clusters.append(cluster)

        return clusters

    def evaluate_cluster(
        self,
        cluster: Dict[str, List[Any]]
    ) -> Tuple[ReconciliationResult, Optional[ReconciliationException]]:
        """
        Applies deterministic rules to evaluate a single candidate cluster.
        Returns a tuple of (ReconciliationResult, Optional[ReconciliationException]).
        """
        gw_list = cluster.get("GATEWAY", [])
        bank_list = cluster.get("BANK", [])
        erp_list = cluster.get("ERP", [])

        # Representative IDs for tracking
        gw_id = gw_list[0].transaction_id if gw_list else None
        bank_id = bank_list[0].transaction_id if bank_list else None
        erp_id = erp_list[0].transaction_id if erp_list else None

        primary_ref = (
            (gw_list[0].reference_id if gw_list and gw_list[0].reference_id else None) or
            (bank_list[0].reference_id if bank_list and bank_list[0].reference_id else None) or
            (erp_list[0].reference_id if erp_list and erp_list[0].reference_id else None) or
            (gw_list[0].order_id if gw_list and gw_list[0].order_id else None) or
            gw_id or bank_id or erp_id or "UNKNOWN"
        )
        
        recon_id = f"REC_{primary_ref}_{uuid.uuid4().hex[:8]}"

        # ==========================================================
        # RULE 1: DUPLICATE TRANSACTIONS
        # ==========================================================
        if len(gw_list) > 1 or len(bank_list) > 1:
            duplicate_src = "GATEWAY" if len(gw_list) > 1 else "BANK"
            dup_count = len(gw_list) if len(gw_list) > 1 else len(bank_list)
            discrepancy = sum(t.amount for t in (gw_list if len(gw_list) > 1 else bank_list))
            
            result = ReconciliationResult(
                reconciliation_id=recon_id,
                gateway_transaction_id=gw_id,
                bank_transaction_id=bank_id,
                erp_invoice_id=erp_id,
                match_score=40.0,
                matching_method="EXACT_RULE",
                ai_recommendation="REVIEW",
                ai_confidence=95.0,
                ai_reasoning=f"{ReconciliationReasonCode.DUPLICATE_TRANSACTION} | Found {dup_count} duplicate records in {duplicate_src}",
                final_decision="HUMAN_REVIEW",
                discrepancy_amount=discrepancy,
                is_resolved=False,
                reconciled_at=datetime.now(timezone.utc)
            )
            exception = ReconciliationException(
                exception_id=f"EXC_{primary_ref}_{uuid.uuid4().hex[:8]}",
                reconciliation_id=recon_id,
                transaction_id=gw_id or bank_id or "DUP_TXN",
                category="DUPLICATE_TRANSACTION",
                severity="HIGH",
                difference_amount=discrepancy,
                ai_explanation=f"Duplicate transactions detected: {dup_count} records found in {duplicate_src} for reference '{primary_ref}'.",
                status="OPEN",
                created_at=datetime.now(timezone.utc)
            )
            return result, exception

        gw = gw_list[0] if gw_list else None
        bnk = bank_list[0] if bank_list else None
        erp = erp_list[0] if erp_list else None

        # ==========================================================
        # RULE 2: FAILED PAYMENT AT GATEWAY
        # ==========================================================
        if gw and gw.status in ("FAILED", "FAILURE", "ERROR"):
            result = ReconciliationResult(
                reconciliation_id=recon_id,
                gateway_transaction_id=gw.transaction_id,
                bank_transaction_id=bnk.transaction_id if bnk else None,
                erp_invoice_id=erp.transaction_id if erp else None,
                match_score=100.0,
                matching_method="EXACT_RULE",
                ai_recommendation="AUTO_RECONCILE",
                ai_confidence=100.0,
                ai_reasoning=ReconciliationReasonCode.FAILED_PAYMENT,
                final_decision="AUTO_RECONCILED",
                discrepancy_amount=0.0,
                is_resolved=True,
                reconciled_at=datetime.now(timezone.utc)
            )
            exception = ReconciliationException(
                exception_id=f"EXC_{primary_ref}_{uuid.uuid4().hex[:8]}",
                reconciliation_id=recon_id,
                transaction_id=gw.transaction_id,
                category="FAILED_PAYMENT",
                severity="LOW",
                difference_amount=0.0,
                ai_explanation=f"Payment {gw.transaction_id} marked as FAILED at Gateway. No settlement was expected.",
                status="RESOLVED",
                created_at=datetime.now(timezone.utc)
            )
            return result, exception

        # ==========================================================
        # RULE 3: MISSING BANK TRANSACTION
        # ==========================================================
        if gw and not bnk:
            result = ReconciliationResult(
                reconciliation_id=recon_id,
                gateway_transaction_id=gw.transaction_id,
                bank_transaction_id=None,
                erp_invoice_id=erp.transaction_id if erp else None,
                match_score=30.0,
                matching_method="EXACT_RULE",
                ai_recommendation="REVIEW",
                ai_confidence=90.0,
                ai_reasoning=f"{ReconciliationReasonCode.MISSING_BANK} | Amount: ₹{gw.amount}",
                final_decision="HUMAN_REVIEW",
                discrepancy_amount=gw.amount,
                is_resolved=False,
                reconciled_at=datetime.now(timezone.utc)
            )
            exception = ReconciliationException(
                exception_id=f"EXC_{primary_ref}_{uuid.uuid4().hex[:8]}",
                reconciliation_id=recon_id,
                transaction_id=gw.transaction_id,
                category="MISSING_BANK_TRANSACTION",
                severity="HIGH",
                difference_amount=gw.amount,
                ai_explanation=f"Gateway captured ₹{gw.amount} for reference '{gw.reference_id}', but no corresponding Bank deposit was received.",
                status="OPEN",
                created_at=datetime.now(timezone.utc)
            )
            return result, exception

        # ==========================================================
        # RULE 4: MISSING GATEWAY TRANSACTION (DIRECT BANK CREDIT)
        # ==========================================================
        if bnk and not gw:
            result = ReconciliationResult(
                reconciliation_id=recon_id,
                gateway_transaction_id=None,
                bank_transaction_id=bnk.transaction_id,
                erp_invoice_id=erp.transaction_id if erp else None,
                match_score=30.0,
                matching_method="EXACT_RULE",
                ai_recommendation="REVIEW",
                ai_confidence=90.0,
                ai_reasoning=f"{ReconciliationReasonCode.MISSING_GATEWAY} | Amount: ₹{bnk.amount}",
                final_decision="HUMAN_REVIEW",
                discrepancy_amount=bnk.amount,
                is_resolved=False,
                reconciled_at=datetime.now(timezone.utc)
            )
            exception = ReconciliationException(
                exception_id=f"EXC_{primary_ref}_{uuid.uuid4().hex[:8]}",
                reconciliation_id=recon_id,
                transaction_id=bnk.transaction_id,
                category="MISSING_GATEWAY_TRANSACTION",
                severity="HIGH",
                difference_amount=bnk.amount,
                ai_explanation=f"Bank statement recorded credit of ₹{bnk.amount} without an associated Gateway transaction.",
                status="OPEN",
                created_at=datetime.now(timezone.utc)
            )
            return result, exception

        # ==========================================================
        # RULE 5: 3-WAY / 2-WAY RECONCILIATION COMPARISONS
        # ==========================================================
        if gw and bnk:
            amount_diff = round(abs(gw.amount - bnk.amount), 2)
            
            # Date difference calculation
            gw_date = gw.transaction_date.replace(tzinfo=timezone.utc) if gw.transaction_date.tzinfo is None else gw.transaction_date
            bnk_date = bnk.transaction_date.replace(tzinfo=timezone.utc) if bnk.transaction_date.tzinfo is None else bnk.transaction_date
            date_diff_days = abs((bnk_date - gw_date).days)

            ref_id_matched = bool(
                gw.reference_id and bnk.reference_id and gw.reference_id == bnk.reference_id
            )
            order_id_matched = bool(
                gw.order_id and bnk.order_id and gw.order_id == bnk.order_id
            )
            ref_matched = ref_id_matched or order_id_matched

            # Check for PARTIAL_PAYMENT
            if erp and erp.amount > gw.amount and (
                getattr(erp, "status", "").upper() == "PARTIALLY_PAID" or
                amount_diff <= self.amount_tolerance
            ):
                partial_diff = round(erp.amount - gw.amount, 2)
                result = ReconciliationResult(
                    reconciliation_id=recon_id,
                    gateway_transaction_id=gw.transaction_id,
                    bank_transaction_id=bnk.transaction_id,
                    erp_invoice_id=erp.transaction_id,
                    match_score=75.0,
                    matching_method="EXACT_RULE",
                    ai_recommendation="REVIEW",
                    ai_confidence=95.0,
                    ai_reasoning=f"{ReconciliationReasonCode.PARTIAL_PAYMENT} | Received: ₹{gw.amount}, Expected: ₹{erp.amount}, Remaining: ₹{partial_diff}",
                    final_decision="HUMAN_REVIEW",
                    discrepancy_amount=partial_diff,
                    is_resolved=False,
                    reconciled_at=datetime.now(timezone.utc)
                )
                exception = ReconciliationException(
                    exception_id=f"EXC_{primary_ref}_{uuid.uuid4().hex[:8]}",
                    reconciliation_id=recon_id,
                    transaction_id=gw.transaction_id,
                    category="PARTIAL_PAYMENT",
                    severity="MEDIUM",
                    difference_amount=partial_diff,
                    ai_explanation=f"Partial payment received: Customer paid ₹{gw.amount} against total invoice amount ₹{erp.amount}.",
                    status="OPEN",
                    created_at=datetime.now(timezone.utc)
                )
                return result, exception

            # Check for AMOUNT_MISMATCH
            if amount_diff > self.amount_tolerance:
                result = ReconciliationResult(
                    reconciliation_id=recon_id,
                    gateway_transaction_id=gw.transaction_id,
                    bank_transaction_id=bnk.transaction_id,
                    erp_invoice_id=erp.transaction_id if erp else None,
                    match_score=50.0,
                    matching_method="EXACT_RULE",
                    ai_recommendation="REVIEW",
                    ai_confidence=95.0,
                    ai_reasoning=f"{ReconciliationReasonCode.AMOUNT_MISMATCH} | Gateway ₹{gw.amount} vs Bank ₹{bnk.amount} (Discrepancy: ₹{amount_diff})",
                    final_decision="HUMAN_REVIEW",
                    discrepancy_amount=amount_diff,
                    is_resolved=False,
                    reconciled_at=datetime.now(timezone.utc)
                )
                exception = ReconciliationException(
                    exception_id=f"EXC_{primary_ref}_{uuid.uuid4().hex[:8]}",
                    reconciliation_id=recon_id,
                    transaction_id=gw.transaction_id,
                    category="AMOUNT_MISMATCH",
                    severity="HIGH",
                    difference_amount=amount_diff,
                    ai_explanation=f"Amount mismatch detected: Gateway recorded ₹{gw.amount}, but Bank recorded ₹{bnk.amount} (Difference: ₹{amount_diff}).",
                    status="OPEN",
                    created_at=datetime.now(timezone.utc)
                )
                return result, exception

            # Check for DATE_MISMATCH (exceeds configured date tolerance)
            if date_diff_days > self.date_tolerance_days:
                result = ReconciliationResult(
                    reconciliation_id=recon_id,
                    gateway_transaction_id=gw.transaction_id,
                    bank_transaction_id=bnk.transaction_id,
                    erp_invoice_id=erp.transaction_id if erp else None,
                    match_score=70.0,
                    matching_method="EXACT_RULE",
                    ai_recommendation="REVIEW",
                    ai_confidence=90.0,
                    ai_reasoning=f"{ReconciliationReasonCode.DATE_MISMATCH} | Gap of {date_diff_days} days exceeds configured tolerance of {self.date_tolerance_days} days",
                    final_decision="HUMAN_REVIEW",
                    discrepancy_amount=0.0,
                    is_resolved=False,
                    reconciled_at=datetime.now(timezone.utc)
                )
                exception = ReconciliationException(
                    exception_id=f"EXC_{primary_ref}_{uuid.uuid4().hex[:8]}",
                    reconciliation_id=recon_id,
                    transaction_id=gw.transaction_id,
                    category="DATE_MISMATCH",
                    severity="MEDIUM",
                    difference_amount=0.0,
                    ai_explanation=f"Settlement date gap of {date_diff_days} days exceeds the allowed tolerance of {self.date_tolerance_days} days.",
                    status="OPEN",
                    created_at=datetime.now(timezone.utc)
                )
                return result, exception

            # Check for REFERENCE_MISMATCH
            # Fires only when BOTH sides carry an explicit reference_id that mismatches.
            # If either side has no reference_id, fall through to EXACT_MATCH.
            if (not ref_id_matched
                    and order_id_matched
                    and gw.reference_id
                    and bnk.reference_id):
                result = ReconciliationResult(
                    reconciliation_id=recon_id,
                    gateway_transaction_id=gw.transaction_id,
                    bank_transaction_id=bnk.transaction_id,
                    erp_invoice_id=erp.transaction_id if erp else None,
                    match_score=65.0,
                    matching_method="EXACT_RULE",
                    ai_recommendation="REVIEW",
                    ai_confidence=85.0,
                    ai_reasoning=f"{ReconciliationReasonCode.REFERENCE_MISMATCH} | Gateway Ref '{gw.reference_id}' vs Bank Ref '{bnk.reference_id}'",
                    final_decision="HUMAN_REVIEW",
                    discrepancy_amount=0.0,
                    is_resolved=False,
                    reconciled_at=datetime.now(timezone.utc)
                )
                exception = ReconciliationException(
                    exception_id=f"EXC_{primary_ref}_{uuid.uuid4().hex[:8]}",
                    reconciliation_id=recon_id,
                    transaction_id=gw.transaction_id,
                    category="REFERENCE_MISMATCH",
                    severity="MEDIUM",
                    difference_amount=0.0,
                    ai_explanation=f"Reference ID mismatch: Gateway ref '{gw.reference_id}' differs from Bank ref '{bnk.reference_id}'.",
                    status="OPEN",
                    created_at=datetime.now(timezone.utc)
                )
                return result, exception

            # ==========================================================
            # PERFECT EXACT MATCH
            # ==========================================================
            result = ReconciliationResult(
                reconciliation_id=recon_id,
                gateway_transaction_id=gw.transaction_id,
                bank_transaction_id=bnk.transaction_id,
                erp_invoice_id=erp.transaction_id if erp else None,
                match_score=100.0,
                matching_method="EXACT_RULE",
                ai_recommendation="AUTO_RECONCILE",
                ai_confidence=100.0,
                ai_reasoning=ReconciliationReasonCode.EXACT_MATCH,
                final_decision="AUTO_RECONCILED",
                discrepancy_amount=0.0,
                is_resolved=True,
                reconciled_at=datetime.now(timezone.utc)
            )
            return result, None

        # Fallback for unexpected or single unhandled item
        single_txn = (gw_list or bank_list or erp_list)[0]
        result = ReconciliationResult(
            reconciliation_id=recon_id,
            gateway_transaction_id=gw_id,
            bank_transaction_id=bank_id,
            erp_invoice_id=erp_id,
            match_score=0.0,
            matching_method="EXACT_RULE",
            ai_recommendation="EXCEPTION",
            ai_confidence=100.0,
            ai_reasoning="UNMATCHED_RECORD | Isolated record with no matching pairs",
            final_decision="EXCEPTION",
            discrepancy_amount=getattr(single_txn, "amount", 0.0),
            is_resolved=False,
            reconciled_at=datetime.now(timezone.utc)
        )
        exception = ReconciliationException(
            exception_id=f"EXC_{primary_ref}_{uuid.uuid4().hex[:8]}",
            reconciliation_id=recon_id,
            transaction_id=getattr(single_txn, "transaction_id", "UNKNOWN"),
            category="EXCEPTION",
            severity="MEDIUM",
            difference_amount=getattr(single_txn, "amount", 0.0),
            ai_explanation="Single transaction record could not be matched with any other source.",
            status="OPEN",
            created_at=datetime.now(timezone.utc)
        )
        return result, exception

    def reconcile_transactions(
        self,
        transactions: List[Union[Transaction, CanonicalTransaction]]
    ) -> Dict[str, Any]:
        """
        Reconciles a list of transactions in-memory and returns structured summary metrics.
        """
        clusters = self.generate_candidate_clusters(transactions)
        results: List[ReconciliationResult] = []
        exceptions: List[ReconciliationException] = []

        for cluster in clusters:
            res, exc = self.evaluate_cluster(cluster)
            results.append(res)
            if exc:
                exceptions.append(exc)

        auto_reconciled_count = sum(1 for r in results if r.final_decision == "AUTO_RECONCILED")
        review_count = sum(1 for r in results if r.final_decision == "HUMAN_REVIEW")
        exception_count = sum(1 for r in results if r.final_decision == "EXCEPTION")

        return {
            "total_clusters": len(clusters),
            "total_reconciled": auto_reconciled_count,
            "total_review": review_count,
            "total_exceptions": len(exceptions),
            "auto_reconciled_rate": round(auto_reconciled_count / len(clusters) * 100, 2) if clusters else 0.0,
            "results": results,
            "exceptions": exceptions
        }

    def run_reconciliation_pipeline(
        self,
        db: Session,
        transactions: Optional[List[Transaction]] = None
    ) -> Dict[str, Any]:
        """
        Executes the reconciliation pipeline and persists all results and exceptions
        into SQLite database within a database transaction.
        """
        if transactions is None:
            transactions = db.query(Transaction).all()

        summary = self.reconcile_transactions(transactions)
        audit_service = AuditService(db=db)

        # Persist results and exceptions with audit logging
        for res in summary["results"]:
            db.add(res)
            target_id = res.gateway_transaction_id or res.bank_transaction_id or res.reconciliation_id
            audit_service.log_action(
                actor="SYSTEM",
                action="RECONCILIATION_COMPLETED",
                entity="RECONCILIATION",
                entity_id=target_id,
                new_value=res.final_decision,
                reason=f"Deterministic matching: recon_id={res.reconciliation_id}, score={res.match_score:.1f}, method={res.matching_method}",
                commit=False,
            )
        for exc in summary["exceptions"]:
            db.add(exc)
            audit_service.log_action(
                actor="SYSTEM",
                action="EXCEPTION_CREATED",
                entity="EXCEPTION",
                entity_id=exc.exception_id,
                new_value=json.dumps({
                    "status": exc.status,
                    "severity": exc.severity,
                    "category": exc.category,
                    "difference_amount": exc.difference_amount,
                }),
                reason=f"Discrepancy in recon_id={exc.reconciliation_id}: {exc.ai_explanation[:300]}",
                commit=False,
            )

        db.commit()

        return summary
