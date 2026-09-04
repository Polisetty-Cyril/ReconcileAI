"""
ReconcileAI - Financial Integrity & Safety Test Suite (Phase 17 Task 2)

Verifies critical financial invariants and safety protections:
1. P0: Zero amount reconciliation ($0.00 promotional/fee-less pairs)
2. P0: Negative refund/reversal transaction reconciliation
3. P0: Sign reversal discrepancy calculation (-500 vs +500 -> 1000 diff)
4. P0: Exact amount-tolerance boundary thresholds (<= tol auto-reconciles, > tol flags mismatch)
5. P0: Extreme amounts (₹999M+) and floating-point fractional safety
6. P0: Source Transaction attributes remain completely unmutated across reconciliation pipeline
7. P0: AI AUTO_RECONCILE recommendation cannot resolve an exception through persist_result()
8. P1: Empty transaction list safety (zero division / graceful handling)
9. P1: Different reviewer cannot re-reject an already rejected exception
10. P1: Cross-session conflicting resolution rejection (sequential independent sessions)
11. P1: Discrepancy amount remains strictly non-negative regardless of source ordering
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.database import SessionLocal, init_db
from backend.models import (
    Transaction,
    ReconciliationResult,
    ReconciliationException,
    AuditLog,
)
from backend.schemas.transaction import CanonicalTransaction
from backend.schemas.ai_controller import AIControllerResult
from backend.services.reconciliation import DeterministicReconciliationEngine
from backend.services.finance_controller import FinanceController
from backend.services.ai_controller import AIController
from backend.services.exception_service import ExceptionManagementService


@pytest.fixture(scope="module", autouse=True)
def init_test_schema():
    """Ensures database schema is initialized in the isolated test database."""
    init_db()


def make_txn(
    txn_id: str,
    source: str,
    ref_id: str = "REF_FIN_001",
    order_id: str = "ORD_FIN_001",
    amount: float = 1000.0,
    currency: str = "INR",
    txn_date: datetime | None = None,
    status: str = "CAPTURED",
    txn_type: str = "PAYMENT",
) -> CanonicalTransaction:
    """Helper to generate canonical transaction records for financial testing."""
    return CanonicalTransaction(
        transaction_id=txn_id,
        source=source,
        reference_id=ref_id,
        order_id=order_id,
        customer_id="CUST_FIN",
        amount=amount,
        currency=currency,
        transaction_date=txn_date or datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc),
        status=status,
        transaction_type=txn_type,
        description=f"Financial integrity test {txn_id}",
        metadata={},
    )


# ===========================================================================
# P0: Financial Amount Edge Cases & Invariants
# ===========================================================================

def test_zero_amount_reconciliation():
    """P0: Verify zero-amount ($0.00) transaction pairs reconcile with zero discrepancy."""
    engine = DeterministicReconciliationEngine(amount_tolerance=0.0)
    gw = make_txn("GW_ZERO", "GATEWAY", ref_id="REF_ZERO", amount=0.0)
    bnk = make_txn("BNK_ZERO", "BANK", ref_id="REF_ZERO", amount=0.0)

    summary = engine.reconcile_transactions([gw, bnk])
    assert summary["total_clusters"] == 1
    assert summary["total_reconciled"] == 1
    assert summary["total_exceptions"] == 0

    result = summary["results"][0]
    assert result.final_decision == "AUTO_RECONCILED"
    assert result.match_score == 100.0
    assert result.discrepancy_amount == 0.0
    assert result.is_resolved is True


def test_negative_refund_reconciliation():
    """P0: Verify matching refund transactions with negative amounts reconcile accurately."""
    engine = DeterministicReconciliationEngine(amount_tolerance=0.0)
    gw = make_txn("GW_REFUND", "GATEWAY", ref_id="REF_REFUND", amount=-1500.0, txn_type="REFUND")
    bnk = make_txn("BNK_REFUND", "BANK", ref_id="REF_REFUND", amount=-1500.0, txn_type="REFUND")

    summary = engine.reconcile_transactions([gw, bnk])
    assert summary["total_clusters"] == 1
    assert summary["total_reconciled"] == 1
    assert summary["total_exceptions"] == 0

    result = summary["results"][0]
    assert result.final_decision == "AUTO_RECONCILED"
    assert result.match_score == 100.0
    assert result.discrepancy_amount == 0.0
    assert result.is_resolved is True


def test_sign_reversal_amount_mismatch():
    """P0: Verify Gateway -500 (refund) vs Bank +500 (credit) correctly calculates difference as 1000."""
    engine = DeterministicReconciliationEngine(amount_tolerance=0.0)
    gw = make_txn("GW_SIGN", "GATEWAY", ref_id="REF_SIGN", amount=-500.0)
    bnk = make_txn("BNK_SIGN", "BANK", ref_id="REF_SIGN", amount=500.0)

    summary = engine.reconcile_transactions([gw, bnk])
    assert summary["total_clusters"] == 1
    assert summary["total_review"] == 1
    assert len(summary["exceptions"]) == 1

    exc = summary["exceptions"][0]
    assert exc.category == "AMOUNT_MISMATCH"
    assert exc.difference_amount == 1000.0
    assert exc.status == "OPEN"

    result = summary["results"][0]
    assert result.final_decision == "HUMAN_REVIEW"
    assert result.discrepancy_amount == 1000.0
    assert result.is_resolved is False


def test_exact_amount_tolerance_boundary():
    """P0: Verify exact tolerance boundary: discrepancy <= tolerance auto-reconciles, > tolerance flags mismatch."""
    tolerance = 0.05
    engine = DeterministicReconciliationEngine(amount_tolerance=tolerance)

    # 1. Exactly on tolerance boundary (diff = 0.05) -> Auto-reconcile
    gw_pass = make_txn("GW_BND_PASS", "GATEWAY", ref_id="REF_BND_PASS", amount=100.00)
    bnk_pass = make_txn("BNK_BND_PASS", "BANK", ref_id="REF_BND_PASS", amount=100.05)
    summary_pass = engine.reconcile_transactions([gw_pass, bnk_pass])
    assert summary_pass["total_reconciled"] == 1
    assert summary_pass["results"][0].final_decision == "AUTO_RECONCILED"

    # 2. Beyond tolerance boundary (diff = 0.06 > 0.05) -> Amount mismatch exception
    gw_fail = make_txn("GW_BND_FAIL", "GATEWAY", ref_id="REF_BND_FAIL", amount=100.00)
    bnk_fail = make_txn("BNK_BND_FAIL", "BANK", ref_id="REF_BND_FAIL", amount=100.06)
    summary_fail = engine.reconcile_transactions([gw_fail, bnk_fail])
    assert summary_fail["total_review"] == 1
    assert len(summary_fail["exceptions"]) == 1
    assert summary_fail["exceptions"][0].category == "AMOUNT_MISMATCH"
    assert summary_fail["exceptions"][0].difference_amount == 0.06


def test_extreme_amounts_and_floating_point_safety():
    """P0: Verify very large amounts (₹999M+) and fractional floating point sums reconcile without precision anomalies."""
    engine = DeterministicReconciliationEngine(amount_tolerance=0.0)

    # Very large transaction
    huge_amount = 999_999_999.99
    gw_huge = make_txn("GW_HUGE", "GATEWAY", ref_id="REF_HUGE", amount=huge_amount)
    bnk_huge = make_txn("BNK_HUGE", "BANK", ref_id="REF_HUGE", amount=huge_amount)

    summary_huge = engine.reconcile_transactions([gw_huge, bnk_huge])
    assert summary_huge["total_reconciled"] == 1
    assert summary_huge["results"][0].discrepancy_amount == 0.0

    # Floating-point representation test: 0.1 + 0.2 = 0.30000000000000004 in IEEE 754
    gw_fp = make_txn("GW_FP", "GATEWAY", ref_id="REF_FP", amount=100.10 + 0.20)
    bnk_fp = make_txn("BNK_FP", "BANK", ref_id="REF_FP", amount=100.30)

    summary_fp = engine.reconcile_transactions([gw_fp, bnk_fp])
    assert summary_fp["total_reconciled"] == 1
    assert summary_fp["results"][0].final_decision == "AUTO_RECONCILED"
    assert summary_fp["results"][0].discrepancy_amount == 0.0


def test_source_transaction_attributes_remain_unchanged():
    """P0: Verify source Transaction records are never mutated by the reconciliation and investigation pipeline."""
    db: Session = SessionLocal()
    try:
        txn_id_gw = f"TX_IMMUT_GW_{uuid.uuid4().hex[:8]}"
        txn_id_bnk = f"TX_IMMUT_BNK_{uuid.uuid4().hex[:8]}"
        ref_id = f"REF_IMMUT_{uuid.uuid4().hex[:8]}"
        order_id = f"ORD_IMMUT_{uuid.uuid4().hex[:8]}"

        t_gw = Transaction(
            transaction_id=txn_id_gw,
            source="GATEWAY",
            reference_id=ref_id,
            order_id=order_id,
            customer_id="CUST_PROTECT",
            amount=4500.0,
            currency="INR",
            transaction_date=datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
            status="CAPTURED",
            transaction_type="PAYMENT",
            description="Original Gateway Payment Description",
            metadata_json='{"tax": 18.0, "fee": 10.0}',
        )
        t_bnk = Transaction(
            transaction_id=txn_id_bnk,
            source="BANK",
            reference_id=ref_id,
            order_id=order_id,
            customer_id="CUST_PROTECT",
            amount=4450.0,  # Discrepancy intentional
            currency="INR",
            transaction_date=datetime(2026, 9, 1, 13, 0, 0, tzinfo=timezone.utc),
            status="CREDIT",
            transaction_type="SETTLEMENT",
            description="Original Bank Credit Description",
            metadata_json='{"bank_branch": "MUMBAI_01"}',
        )
        db.add_all([t_gw, t_bnk])
        db.commit()

        # Capture complete pre-reconciliation attribute snapshots
        snapshot_gw = {
            "amount": t_gw.amount,
            "currency": t_gw.currency,
            "status": t_gw.status,
            "transaction_type": t_gw.transaction_type,
            "reference_id": t_gw.reference_id,
            "order_id": t_gw.order_id,
            "description": t_gw.description,
            "metadata_json": t_gw.metadata_json,
        }
        snapshot_bnk = {
            "amount": t_bnk.amount,
            "currency": t_bnk.currency,
            "status": t_bnk.status,
            "transaction_type": t_bnk.transaction_type,
            "reference_id": t_bnk.reference_id,
            "order_id": t_bnk.order_id,
            "description": t_bnk.description,
            "metadata_json": t_bnk.metadata_json,
        }

        # Execute full reconciliation and investigation pipeline
        controller = FinanceController(db=db)
        summary = controller.reconcile_and_investigate(transactions=[t_gw, t_bnk], db=db, persist=True)
        db.commit()

        # Refresh instances from database
        db.refresh(t_gw)
        db.refresh(t_bnk)

        # Assert every single financial attribute remained 100% untouched
        for attr, val in snapshot_gw.items():
            assert getattr(t_gw, attr) == val, f"Gateway Transaction.{attr} mutated from {val} to {getattr(t_gw, attr)}"

        for attr, val in snapshot_bnk.items():
            assert getattr(t_bnk, attr) == val, f"Bank Transaction.{attr} mutated from {val} to {getattr(t_bnk, attr)}"
    finally:
        db.close()


def test_ai_recommendation_cannot_resolve_exception_in_persistence():
    """P0: Verify that AI AUTO_RECONCILE recommendation in persist_result() strictly leaves exception unresolved."""
    db: Session = SessionLocal()
    try:
        recon_id = f"REC_SAFETY_{uuid.uuid4().hex[:8]}"
        exc_id = f"EXC_SAFETY_{uuid.uuid4().hex[:8]}"

        result = ReconciliationResult(
            reconciliation_id=recon_id,
            gateway_transaction_id="GW_SAFETY_01",
            match_score=75.0,
            matching_method="RULE_BASED",
            final_decision="HUMAN_REVIEW",
            discrepancy_amount=150.0,
            is_resolved=False,
        )
        exc = ReconciliationException(
            exception_id=exc_id,
            reconciliation_id=recon_id,
            transaction_id="GW_SAFETY_01",
            category="AMOUNT_MISMATCH",
            severity="HIGH",
            difference_amount=150.0,
            status="OPEN",
        )
        db.add_all([result, exc])
        db.commit()

        # Construct an AI result that advises AUTO_RECONCILE with 100% confidence
        ai_advice = AIControllerResult(
            recommendation="AUTO_RECONCILE",
            confidence=1.0,
            reason="AI strongly recommends auto-reconciling this difference.",
            risk="LOW",
        )

        ai_controller = AIController()
        ai_controller.persist_result(db=db, result=result, ai_result=ai_advice, exception=exc)
        db.commit()

        db.refresh(result)
        db.refresh(exc)

        # CRITICAL SAFETY INVARIANTS:
        # 1. ReconciliationResult.is_resolved must remain False
        assert result.is_resolved is False, "AI advisory autonomously resolved ReconciliationResult!"
        # 2. ReconciliationException.status must remain OPEN
        assert exc.status == "OPEN", "AI advisory autonomously altered ReconciliationException.status!"
        # 3. Human resolver fields must remain None
        assert exc.resolved_by is None
        assert exc.resolved_at is None
        # 4. AI recommendation is recorded purely as advisory
        assert result.ai_recommendation == "AUTO_RECONCILE"
        assert exc.ai_explanation == ai_advice.reason
    finally:
        db.close()


# ===========================================================================
# P1: Error Handling, Concurrency & Resolution Edge Cases
# ===========================================================================

def test_empty_transaction_list_safety():
    """P1: Verify empty transaction list returns zero metrics without divide-by-zero crashes."""
    engine = DeterministicReconciliationEngine()
    summary = engine.reconcile_transactions([])

    assert summary["total_clusters"] == 0
    assert summary["total_reconciled"] == 0
    assert summary["total_review"] == 0
    assert summary["total_exceptions"] == 0
    assert summary["auto_reconciled_rate"] == 0.0
    assert summary["results"] == []
    assert summary["exceptions"] == []


def test_different_reviewer_cannot_re_reject_exception():
    """P1: Verify that a different reviewer cannot overwrite an already rejected exception."""
    db: Session = SessionLocal()
    try:
        exc_id = f"EXC_REJECT_CONFLICT_{uuid.uuid4().hex[:8]}"
        exc = ReconciliationException(
            exception_id=exc_id,
            transaction_id="GW_TXN_001",
            category="AMOUNT_MISMATCH",
            severity="MEDIUM",
            difference_amount=50.0,
            status="OPEN",
        )
        db.add(exc)
        db.commit()

        # Reviewer 1 rejects
        ExceptionManagementService.reject_exception(
            db=db,
            exception_id=exc_id,
            reviewer_id="reviewer_alice",
            notes="Rejected by Alice",
        )

        # Reviewer 2 attempts to reject the already rejected exception -> HTTP 400
        with pytest.raises(HTTPException) as exc_info:
            ExceptionManagementService.reject_exception(
                db=db,
                exception_id=exc_id,
                reviewer_id="reviewer_bob",
                notes="Rejected by Bob",
            )
        assert exc_info.value.status_code == 400
        assert "already rejected by different reviewer" in exc_info.value.detail.lower()
    finally:
        db.close()


def test_cross_session_conflicting_resolution_rejected():
    """
    P1: Verify cross-session state-machine conflict handling across two independent SQLAlchemy sessions.

    Demonstrates that:
    1. Two distinct sessions (db1, db2) connect to the same database engine.
    2. Session 1 approves and commits an exception.
    3. Session 2 subsequently attempts to reject that same exception.
    4. The ExceptionManagementService state machine rejects the conflicting transition (HTTP 400).
    5. The persisted database state strictly retains APPROVED (initial decision is immutable).

    Note: This verifies sequential cross-session conflict validation, not low-level thread race condition locking.
    """
    db1: Session = SessionLocal()
    db2: Session = SessionLocal()
    try:
        exc_id = f"EXC_CROSS_SESSION_{uuid.uuid4().hex[:8]}"
        recon_id = f"REC_CROSS_SESSION_{uuid.uuid4().hex[:8]}"

        init_exc = ReconciliationException(
            exception_id=exc_id,
            reconciliation_id=recon_id,
            transaction_id="GW_TXN_002",
            category="AMOUNT_MISMATCH",
            severity="HIGH",
            difference_amount=75.0,
            status="OPEN",
        )
        init_recon = ReconciliationResult(
            reconciliation_id=recon_id,
            match_score=60.0,
            matching_method="RULE_BASED",
            final_decision="HUMAN_REVIEW",
            discrepancy_amount=75.0,
            is_resolved=False,
        )
        db1.add_all([init_exc, init_recon])
        db1.commit()

        # Step 1: Session 1 approves and commits the exception
        ExceptionManagementService.approve_exception(
            db=db1,
            exception_id=exc_id,
            reviewer_id="operator_alice",
            notes="Approved by Alice in Session 1",
        )

        # Step 2: Session 2 subsequently attempts to reject the same exception
        with pytest.raises(HTTPException) as exc_info:
            ExceptionManagementService.reject_exception(
                db=db2,
                exception_id=exc_id,
                reviewer_id="operator_bob",
                notes="Rejected by Bob in Session 2",
            )

        # Step 3: Service state machine blocks the conflicting transition
        assert exc_info.value.status_code == 400
        assert "already approved by operator_alice" in exc_info.value.detail.lower()

        # Step 4: Final database state remains APPROVED by operator_alice
        db1.expire_all()
        persisted_exc = db1.query(ReconciliationException).filter_by(exception_id=exc_id).first()
        assert persisted_exc.status == "APPROVED"
        assert persisted_exc.resolved_by == "operator_alice"

        persisted_recon = db1.query(ReconciliationResult).filter_by(reconciliation_id=recon_id).first()
        assert persisted_recon.is_resolved is True
        assert persisted_recon.final_decision == "MANUAL_APPROVED"
    finally:
        db1.close()
        db2.close()


def test_discrepancy_amount_strictly_non_negative_reversed_ordering():
    """P1: Verify discrepancy_amount and difference_amount are always non-negative regardless of source ordering."""
    engine = DeterministicReconciliationEngine(amount_tolerance=0.0)

    # Case A: Gateway (1000.0) > Bank (700.0) -> diff = 300.0
    gw_a = make_txn("GW_ORD_A", "GATEWAY", ref_id="REF_ORD_A", amount=1000.0)
    bnk_a = make_txn("BNK_ORD_A", "BANK", ref_id="REF_ORD_A", amount=700.0)
    res_a = engine.reconcile_transactions([gw_a, bnk_a])
    assert res_a["results"][0].discrepancy_amount == 300.0
    assert res_a["exceptions"][0].difference_amount == 300.0
    assert res_a["results"][0].discrepancy_amount >= 0.0
    assert res_a["exceptions"][0].difference_amount >= 0.0

    # Case B: Gateway (700.0) < Bank (1000.0) -> diff = 300.0 (strictly non-negative, never -300.0)
    gw_b = make_txn("GW_ORD_B", "GATEWAY", ref_id="REF_ORD_B", amount=700.0)
    bnk_b = make_txn("BNK_ORD_B", "BANK", ref_id="REF_ORD_B", amount=1000.0)
    res_b = engine.reconcile_transactions([gw_b, bnk_b])
    assert res_b["results"][0].discrepancy_amount == 300.0
    assert res_b["exceptions"][0].difference_amount == 300.0
    assert res_b["results"][0].discrepancy_amount >= 0.0
    assert res_b["exceptions"][0].difference_amount >= 0.0
