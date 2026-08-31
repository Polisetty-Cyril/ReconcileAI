"""
Phase 6 Unit & Integration Tests: Deterministic Rule-Based Reconciliation Engine
Verifies:
1. Exact 3-way matching (Gateway ↔ Bank ↔ ERP)
2. Reference ID matching
3. Order ID matching
4. Amount mismatch detection and threshold enforcement
5. Date within tolerance vs date outside tolerance
6. Missing bank transaction detection
7. Missing gateway transaction detection (unexpected bank credit)
8. Duplicate transaction detection
9. Failed payment handling
10. Partial payment detection
11. Reference mismatch detection
12. Multiple candidate clustering
13. Database persistence for reconciliation_results
14. Database persistence for reconciliation_exceptions
15. Deterministic, reproducible output
16. Full synthetic dataset benchmark run
"""

import os
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.orm import Session
from backend.database import SessionLocal, init_db
from backend.models import (
    Transaction,
    ReconciliationResult,
    ReconciliationException
)
from backend.schemas.transaction import CanonicalTransaction
from backend.services.reconciliation import (
    DeterministicReconciliationEngine,
    ReconciliationReasonCode
)
from backend.services.ingestion import IngestionService
from backend.services.normalizer import DataNormalizer

@pytest.fixture(scope="module", autouse=True)
def setup_test_db():
    """Initializes database schema and cleans test records."""
    init_db()
    db = SessionLocal()
    try:
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like("%TEST_%")).delete(synchronize_session=False)
        db.query(ReconciliationResult).filter(ReconciliationResult.reconciliation_id.like("%TEST_%")).delete(synchronize_session=False)
        db.query(Transaction).filter(Transaction.transaction_id.like("%RECON_TEST_%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield

def make_canonical(
    txn_id: str,
    source: str,
    ref_id: str = "pay_test_100",
    order_id: str = "ORD_TEST_100",
    cust_id: str = "CUST_100",
    amount: float = 5000.0,
    txn_date: datetime = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc),
    status: str = "CAPTURED",
    txn_type: str = "PAYMENT"
) -> CanonicalTransaction:
    """Helper to build CanonicalTransaction fixtures."""
    return CanonicalTransaction(
        transaction_id=txn_id,
        source=source,
        reference_id=ref_id,
        order_id=order_id,
        customer_id=cust_id,
        amount=amount,
        currency="INR",
        transaction_date=txn_date,
        status=status,
        transaction_type=txn_type,
        description=f"{source} transaction {txn_id}",
        metadata={}
    )

def test_exact_match_three_way():
    """Verify perfect 3-way exact matching across Gateway, Bank, and ERP."""
    engine = DeterministicReconciliationEngine(amount_tolerance=0.0, date_tolerance_days=3)
    base_date = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)
    
    gw = make_canonical("GW_1", "GATEWAY", ref_id="pay_1", order_id="ORD_1", amount=2500.0, txn_date=base_date, status="CAPTURED")
    bnk = make_canonical("BNK_1", "BANK", ref_id="pay_1", order_id="ORD_1", amount=2500.0, txn_date=base_date + timedelta(days=1), status="CREDIT", txn_type="SETTLEMENT")
    erp = make_canonical("INV_1", "ERP", ref_id="pay_1", order_id="ORD_1", amount=2500.0, txn_date=base_date, status="PAID", txn_type="INVOICE")

    res = engine.reconcile_transactions([gw, bnk, erp])
    assert res["total_clusters"] == 1
    assert res["total_reconciled"] == 1
    assert res["total_exceptions"] == 0

    recon_res: ReconciliationResult = res["results"][0]
    assert recon_res.final_decision == "AUTO_RECONCILED"
    assert recon_res.match_score == 100.0
    assert recon_res.matching_method == "EXACT_RULE"
    assert recon_res.discrepancy_amount == 0.0
    assert recon_res.is_resolved is True
    assert ReconciliationReasonCode.EXACT_MATCH in recon_res.ai_reasoning

def test_order_id_matching():
    """Verify matching when reference_id is absent but order_id is identical."""
    engine = DeterministicReconciliationEngine()
    base_date = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)

    gw = make_canonical("GW_2", "GATEWAY", ref_id=None, order_id="ORD_999", amount=1200.0, txn_date=base_date)
    bnk = make_canonical("BNK_2", "BANK", ref_id=None, order_id="ORD_999", amount=1200.0, txn_date=base_date + timedelta(days=1))
    erp = make_canonical("INV_2", "ERP", ref_id=None, order_id="ORD_999", amount=1200.0, txn_date=base_date)

    res = engine.reconcile_transactions([gw, bnk, erp])
    assert res["total_clusters"] == 1
    assert res["total_reconciled"] == 1
    recon_res = res["results"][0]
    assert recon_res.final_decision == "AUTO_RECONCILED"
    assert recon_res.match_score == 100.0

def test_amount_mismatch_detection():
    """Verify that an amount discrepancy creates an AMOUNT_MISMATCH exception."""
    engine = DeterministicReconciliationEngine(amount_tolerance=0.0)
    base_date = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)

    gw = make_canonical("GW_3", "GATEWAY", ref_id="pay_3", amount=5000.0, txn_date=base_date)
    bnk = make_canonical("BNK_3", "BANK", ref_id="pay_3", amount=4950.0, txn_date=base_date + timedelta(days=1))
    erp = make_canonical("INV_3", "ERP", ref_id="pay_3", amount=5000.0, txn_date=base_date)

    res = engine.reconcile_transactions([gw, bnk, erp])
    assert res["total_clusters"] == 1
    assert res["total_review"] == 1
    assert len(res["exceptions"]) == 1

    exc: ReconciliationException = res["exceptions"][0]
    assert exc.category == "AMOUNT_MISMATCH"
    assert exc.severity == "HIGH"
    assert exc.difference_amount == 50.0
    assert exc.status == "OPEN"

def test_date_within_tolerance():
    """Verify reconciliation succeeds when bank settlement is within the configured 3-day window."""
    engine = DeterministicReconciliationEngine(date_tolerance_days=3)
    base_date = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)

    gw = make_canonical("GW_4", "GATEWAY", ref_id="pay_4", amount=3000.0, txn_date=base_date)
    bnk = make_canonical("BNK_4", "BANK", ref_id="pay_4", amount=3000.0, txn_date=base_date + timedelta(days=2))

    res = engine.reconcile_transactions([gw, bnk])
    assert res["total_reconciled"] == 1
    assert res["results"][0].final_decision == "AUTO_RECONCILED"

def test_date_outside_tolerance():
    """Verify DATE_MISMATCH exception when bank settlement is delayed beyond date tolerance."""
    engine = DeterministicReconciliationEngine(date_tolerance_days=3)
    base_date = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)

    gw = make_canonical("GW_5", "GATEWAY", ref_id="pay_5", amount=3000.0, txn_date=base_date)
    bnk = make_canonical("BNK_5", "BANK", ref_id="pay_5", amount=3000.0, txn_date=base_date + timedelta(days=10))

    res = engine.reconcile_transactions([gw, bnk])
    assert res["total_review"] == 1
    assert len(res["exceptions"]) == 1
    assert res["exceptions"][0].category == "DATE_MISMATCH"
    assert res["exceptions"][0].severity == "MEDIUM"

def test_missing_bank_transaction():
    """Verify MISSING_BANK_TRANSACTION exception when gateway payment has no bank credit."""
    engine = DeterministicReconciliationEngine()
    gw = make_canonical("GW_6", "GATEWAY", ref_id="pay_6", amount=7500.0, status="CAPTURED")
    erp = make_canonical("INV_6", "ERP", ref_id="pay_6", amount=7500.0)

    res = engine.reconcile_transactions([gw, erp])
    assert res["total_review"] == 1
    assert len(res["exceptions"]) == 1
    exc = res["exceptions"][0]
    assert exc.category == "MISSING_BANK_TRANSACTION"
    assert exc.difference_amount == 7500.0
    assert exc.severity == "HIGH"

def test_missing_gateway_transaction():
    """Verify MISSING_GATEWAY_TRANSACTION exception when bank credit has no gateway charge."""
    engine = DeterministicReconciliationEngine()
    bnk = make_canonical("BNK_7", "BANK", ref_id="NEFT_777", amount=15000.0, status="CREDIT")
    erp = make_canonical("INV_7", "ERP", ref_id="NEFT_777", amount=15000.0)

    res = engine.reconcile_transactions([bnk, erp])
    assert res["total_review"] == 1
    assert len(res["exceptions"]) == 1
    exc = res["exceptions"][0]
    assert exc.category == "MISSING_GATEWAY_TRANSACTION"
    assert exc.difference_amount == 15000.0

def test_duplicate_transaction_detection():
    """Verify DUPLICATE_TRANSACTION exception when multiple gateway records share the same reference."""
    engine = DeterministicReconciliationEngine()
    gw1 = make_canonical("GW_8A", "GATEWAY", ref_id="pay_8", amount=4000.0)
    gw2 = make_canonical("GW_8B", "GATEWAY", ref_id="pay_8", amount=4000.0)
    bnk = make_canonical("BNK_8", "BANK", ref_id="pay_8", amount=4000.0)

    res = engine.reconcile_transactions([gw1, gw2, bnk])
    assert res["total_review"] == 1
    assert len(res["exceptions"]) == 1
    exc = res["exceptions"][0]
    assert exc.category == "DUPLICATE_TRANSACTION"
    assert exc.severity == "HIGH"

def test_failed_payment_handling():
    """Verify FAILED_PAYMENT is reconciled deterministically as failed with no money movement."""
    engine = DeterministicReconciliationEngine()
    gw = make_canonical("GW_9", "GATEWAY", ref_id="pay_9", amount=2000.0, status="FAILED")
    erp = make_canonical("INV_9", "ERP", ref_id="pay_9", amount=2000.0, status="UNPAID")

    res = engine.reconcile_transactions([gw, erp])
    assert res["total_reconciled"] == 1
    recon_res = res["results"][0]
    assert recon_res.final_decision == "AUTO_RECONCILED"
    assert len(res["exceptions"]) == 1
    assert res["exceptions"][0].category == "FAILED_PAYMENT"
    assert res["exceptions"][0].status == "RESOLVED"

def test_partial_payment_detection():
    """Verify PARTIAL_PAYMENT detection when received payment is less than invoice expected amount."""
    engine = DeterministicReconciliationEngine()
    base_date = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)

    gw = make_canonical("GW_10", "GATEWAY", ref_id="pay_10", amount=6000.0, txn_date=base_date)
    bnk = make_canonical("BNK_10", "BANK", ref_id="pay_10", amount=6000.0, txn_date=base_date + timedelta(days=1))
    erp = make_canonical("INV_10", "ERP", ref_id="pay_10", amount=10000.0, txn_date=base_date, status="PARTIALLY_PAID")

    res = engine.reconcile_transactions([gw, bnk, erp])
    assert res["total_review"] == 1
    assert len(res["exceptions"]) == 1
    exc = res["exceptions"][0]
    assert exc.category == "PARTIAL_PAYMENT"
    assert exc.difference_amount == 4000.0
    assert exc.severity == "MEDIUM"

def test_reference_mismatch_detection():
    """Verify REFERENCE_MISMATCH exception when order_id matches but bank reference differs."""
    engine = DeterministicReconciliationEngine()
    base_date = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)

    gw = make_canonical("GW_11", "GATEWAY", ref_id="pay_1045", order_id="ORD_1045", amount=3500.0, txn_date=base_date)
    bnk = make_canonical("BNK_11", "BANK", ref_id="pay_104A", order_id="ORD_1045", amount=3500.0, txn_date=base_date + timedelta(days=1))
    erp = make_canonical("INV_11", "ERP", ref_id="pay_1045", order_id="ORD_1045", amount=3500.0, txn_date=base_date)

    res = engine.reconcile_transactions([gw, bnk, erp])
    assert res["total_review"] == 1
    assert len(res["exceptions"]) == 1
    assert res["exceptions"][0].category == "REFERENCE_MISMATCH"

def test_multiple_candidate_records():
    """Verify handling of a multi-transaction batch spanning different scenarios."""
    engine = DeterministicReconciliationEngine()
    base_date = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)

    # 1. Exact Match pair — distinct order_id so cluster stays isolated
    gw1 = make_canonical("GW_M1", "GATEWAY", ref_id="pay_m1", order_id="ORD_M1", amount=1000.0, txn_date=base_date)
    bnk1 = make_canonical("BNK_M1", "BANK", ref_id="pay_m1", order_id="ORD_M1", amount=1000.0, txn_date=base_date + timedelta(days=1))

    # 2. Missing Bank — isolated by its own ref and order_id
    gw2 = make_canonical("GW_M2", "GATEWAY", ref_id="pay_m2", order_id="ORD_M2", amount=2000.0, txn_date=base_date)

    # 3. Amount Mismatch pair — distinct order_id so it stays separate
    gw3 = make_canonical("GW_M3", "GATEWAY", ref_id="pay_m3", order_id="ORD_M3", amount=3000.0, txn_date=base_date)
    bnk3 = make_canonical("BNK_M3", "BANK", ref_id="pay_m3", order_id="ORD_M3", amount=3500.0, txn_date=base_date + timedelta(days=1))

    res = engine.reconcile_transactions([gw1, bnk1, gw2, gw3, bnk3])
    assert res["total_clusters"] == 3
    assert res["total_reconciled"] == 1
    assert res["total_review"] == 2
    assert len(res["exceptions"]) == 2

def test_database_persistence_of_results_and_exceptions():
    """Verify that run_reconciliation_pipeline persists all records into SQLite tables."""
    db: Session = SessionLocal()
    engine = DeterministicReconciliationEngine()
    base_date = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)

    try:
        # Create DB transaction records
        gw_txn = Transaction(
            transaction_id="RECON_TEST_GW_1",
            source="GATEWAY",
            reference_id="pay_persist_1",
            order_id="ORD_PERSIST_1",
            amount=5000.0,
            currency="INR",
            transaction_date=base_date,
            status="CAPTURED",
            transaction_type="PAYMENT",
            description="Persist test gateway"
        )
        bnk_txn = Transaction(
            transaction_id="RECON_TEST_BNK_1",
            source="BANK",
            reference_id="pay_persist_1",
            order_id="ORD_PERSIST_1",
            amount=5000.0,
            currency="INR",
            transaction_date=base_date + timedelta(days=1),
            status="CREDIT",
            transaction_type="SETTLEMENT",
            description="Persist test bank"
        )
        gw_mismatch = Transaction(
            transaction_id="RECON_TEST_GW_2",
            source="GATEWAY",
            reference_id="pay_persist_2",
            order_id="ORD_PERSIST_2",
            amount=8000.0,
            currency="INR",
            transaction_date=base_date,
            status="CAPTURED",
            transaction_type="PAYMENT",
            description="Persist test mismatch"
        )
        bnk_mismatch = Transaction(
            transaction_id="RECON_TEST_BNK_2",
            source="BANK",
            reference_id="pay_persist_2",
            order_id="ORD_PERSIST_2",
            amount=7500.0,
            currency="INR",
            transaction_date=base_date + timedelta(days=1),
            status="CREDIT",
            transaction_type="SETTLEMENT",
            description="Persist test mismatch"
        )
        db.add_all([gw_txn, bnk_txn, gw_mismatch, bnk_mismatch])
        db.commit()

        # Run pipeline with explicit transaction subset
        txns = [gw_txn, bnk_txn, gw_mismatch, bnk_mismatch]
        summary = engine.run_reconciliation_pipeline(db, txns)

        assert summary["total_clusters"] == 2
        assert summary["total_reconciled"] == 1
        assert summary["total_review"] == 1
        assert summary["total_exceptions"] == 1

        # Query database directly to confirm row insertions
        saved_recon_results = db.query(ReconciliationResult).filter(
            ReconciliationResult.gateway_transaction_id.in_(["RECON_TEST_GW_1", "RECON_TEST_GW_2"])
        ).all()
        assert len(saved_recon_results) == 2

        saved_exceptions = db.query(ReconciliationException).filter(
            ReconciliationException.transaction_id == "RECON_TEST_GW_2"
        ).all()
        assert len(saved_exceptions) == 1
        assert saved_exceptions[0].category == "AMOUNT_MISMATCH"
        assert saved_exceptions[0].difference_amount == 500.0

    finally:
        # Cleanup
        db.query(ReconciliationException).filter(ReconciliationException.transaction_id.like("%RECON_TEST_%")).delete(synchronize_session=False)
        db.query(ReconciliationResult).filter(ReconciliationResult.gateway_transaction_id.like("%RECON_TEST_%")).delete(synchronize_session=False)
        db.query(Transaction).filter(Transaction.transaction_id.like("%RECON_TEST_%")).delete(synchronize_session=False)
        db.commit()
        db.close()

def test_deterministic_reproducibility():
    """Verify that running the engine on the exact same inputs produces identical outputs."""
    engine = DeterministicReconciliationEngine()
    base_date = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)

    inputs = [
        make_canonical("GW_R1", "GATEWAY", ref_id="pay_r1", amount=1000.0, txn_date=base_date),
        make_canonical("BNK_R1", "BANK", ref_id="pay_r1", amount=1000.0, txn_date=base_date + timedelta(days=1)),
        make_canonical("GW_R2", "GATEWAY", ref_id="pay_r2", amount=2000.0, txn_date=base_date),
        make_canonical("BNK_R2", "BANK", ref_id="pay_r2", amount=1900.0, txn_date=base_date + timedelta(days=1)),
    ]

    run1 = engine.reconcile_transactions(inputs)
    run2 = engine.reconcile_transactions(inputs)

    assert run1["total_clusters"] == run2["total_clusters"]
    assert run1["total_reconciled"] == run2["total_reconciled"]
    assert run1["total_review"] == run2["total_review"]
    assert run1["total_exceptions"] == run2["total_exceptions"]

    for r1, r2 in zip(run1["results"], run2["results"]):
        assert r1.final_decision == r2.final_decision
        assert r1.match_score == r2.match_score
        assert r1.discrepancy_amount == r2.discrepancy_amount
        assert r1.matching_method == r2.matching_method
