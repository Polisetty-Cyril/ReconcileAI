"""
Phase 5 Unit Tests: Data Normalization Layer & Ingestion Service
Verifies:
1. CanonicalTransaction Pydantic schema validation
2. parse_amount handling currency symbols, commas, negative values, and NaNs
3. parse_datetime handling multiple formats (ISO, YYYY-MM-DD, DD/MM/YYYY)
4. clean_string handling whitespace and nulls
5. extract_reference_from_description regex logic
6. Normalization for Payment Gateway, Bank Statement, and ERP Ledger
7. Batch normalization
8. IngestionService database persistence
"""

import os
from datetime import datetime, timezone
import pytest
from backend.database import SessionLocal, init_db
from backend.models.transaction import Transaction
from backend.schemas.transaction import CanonicalTransaction
from backend.services.normalizer import (
    DataNormalizer,
    parse_amount,
    parse_datetime,
    clean_string,
    extract_reference_from_description
)
from backend.services.ingestion import IngestionService

@pytest.fixture(scope="module", autouse=True)
def setup_db():
    init_db()
    db = SessionLocal()
    try:
        db.query(Transaction).filter(Transaction.transaction_id.like("%NORM_TEST%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield

def test_parse_amount():
    """Verify amount cleaning across various raw string and numeric formats."""
    assert parse_amount(5000) == 5000.0
    assert parse_amount(5000.50) == 5000.50
    assert parse_amount("5000") == 5000.0
    assert parse_amount("₹ 4,500.50") == 4500.50
    assert parse_amount("  12,999.00 INR ") == 12999.0
    assert parse_amount(-250.0) == 250.0  # Absolute value
    assert parse_amount(None) == 0.0
    assert parse_amount("NaN") == 0.0
    assert parse_amount("") == 0.0

def test_parse_datetime():
    """Verify datetime parsing across ISO, standard, and regional formats."""
    dt_iso = parse_datetime("2026-08-25T10:30:00")
    assert dt_iso.year == 2026 and dt_iso.month == 8 and dt_iso.day == 25
    assert dt_iso.tzinfo is not None

    dt_ymd = parse_datetime("2026-08-01 14:20:00")
    assert dt_ymd.year == 2026 and dt_ymd.hour == 14

    dt_short = parse_datetime("2026-08-15")
    assert dt_short.year == 2026 and dt_short.month == 8 and dt_short.day == 15

    # Fallback on null/empty
    dt_none = parse_datetime(None)
    assert isinstance(dt_none, datetime)

def test_clean_string():
    """Verify whitespace trimming and null/NaN elimination."""
    assert clean_string("  ORD1001  ") == "ORD1001"
    assert clean_string(None) is None
    assert clean_string("NaN") is None
    assert clean_string("   ") is None

def test_extract_reference_from_description():
    """Verify reference extraction from unstructured bank narration strings."""
    assert extract_reference_from_description("Razorpay Settlement - pay_10234 / ORD1001") == "pay_10234"
    assert extract_reference_from_description("Direct Credit NEFT_998812 from Customer 001") == "NEFT_998812"
    assert extract_reference_from_description("Payout for ORD5542") == "ORD5542"
    assert extract_reference_from_description("General bank service charge") is None

def test_normalize_gateway_transaction():
    """Verify transformation of raw Payment Gateway records into CanonicalTransaction."""
    raw = {
        "gateway_transaction_id": "GW_NORM_TEST_1",
        "payment_id": "pay_norm_001",
        "order_id": "ORD_NORM_001",
        "customer_id": "CUST_001",
        "amount": " ₹ 3,500.00 ",
        "currency": "inr",
        "payment_method": "UPI",
        "status": "captured",
        "transaction_date": "2026-08-10 10:15:00",
        "captured_at": "2026-08-10 10:15:00",
        "fee": "70.00",
        "tax": "12.60",
        "net_amount": "3417.40"
    }
    canonical = DataNormalizer.normalize_gateway(raw)

    assert isinstance(canonical, CanonicalTransaction)
    assert canonical.transaction_id == "GW_NORM_TEST_1"
    assert canonical.source == "GATEWAY"
    assert canonical.reference_id == "pay_norm_001"
    assert canonical.order_id == "ORD_NORM_001"
    assert canonical.customer_id == "CUST_001"
    assert canonical.amount == 3500.00
    assert canonical.currency == "INR"
    assert canonical.status == "CAPTURED"
    assert canonical.transaction_type == "PAYMENT"
    assert canonical.metadata.get("fee") == 70.00
    assert canonical.metadata.get("payment_method") == "UPI"

def test_normalize_bank_transaction():
    """Verify transformation of raw Bank Statement records into CanonicalTransaction."""
    raw = {
        "bank_transaction_id": "BNK_NORM_TEST_1",
        "bank_reference": "pay_norm_001",
        "transaction_date": "2026-08-11",
        "value_date": "2026-08-11",
        "description": "Settlement Payout - pay_norm_001",
        "credit_amount": " ₹ 3,500.00 ",
        "debit_amount": 0.0,
        "balance": "500000.00",
        "bank_account": "HDFC_DEMO_009988",
        "transaction_type": "CREDIT"
    }
    canonical = DataNormalizer.normalize_bank(raw)

    assert isinstance(canonical, CanonicalTransaction)
    assert canonical.transaction_id == "BNK_NORM_TEST_1"
    assert canonical.source == "BANK"
    assert canonical.reference_id == "pay_norm_001"
    assert canonical.amount == 3500.00
    assert canonical.status == "CREDIT"
    assert canonical.transaction_type == "SETTLEMENT"
    assert canonical.metadata.get("bank_account") == "HDFC_DEMO_009988"

def test_normalize_erp_transaction():
    """Verify transformation of raw ERP Ledger records into CanonicalTransaction."""
    raw = {
        "invoice_id": "INV_NORM_TEST_1",
        "order_id": "ORD_NORM_001",
        "customer_id": "CUST_001",
        "customer_name": "Customer 001",
        "invoice_amount": " ₹ 3,500.00 ",
        "expected_payment": 3500.00,
        "invoice_date": "2026-08-10",
        "payment_status": "PAID",
        "reference_id": "pay_norm_001"
    }
    canonical = DataNormalizer.normalize_erp(raw)

    assert isinstance(canonical, CanonicalTransaction)
    assert canonical.transaction_id == "INV_NORM_TEST_1"
    assert canonical.source == "ERP"
    assert canonical.reference_id == "pay_norm_001"
    assert canonical.order_id == "ORD_NORM_001"
    assert canonical.customer_id == "CUST_001"
    assert canonical.amount == 3500.00
    assert canonical.status == "PAID"
    assert canonical.transaction_type == "INVOICE"
    assert canonical.metadata.get("customer_name") == "Customer 001"

def test_normalize_batch():
    """Verify batch normalization across multiple records."""
    records = [
        {"gateway_transaction_id": "GW_NORM_TEST_10", "payment_id": "p1", "amount": 1000, "status": "captured"},
        {"gateway_transaction_id": "GW_NORM_TEST_11", "payment_id": "p2", "amount": 2000, "status": "captured"}
    ]
    batch = DataNormalizer.normalize_batch(records, source="GATEWAY")
    assert len(batch) == 2
    assert batch[0].amount == 1000.0
    assert batch[1].amount == 2000.0

def test_ingestion_service_persistence():
    """Verify that IngestionService normalizes and persists records into the SQLite transactions table."""
    db = SessionLocal()
    try:
        raw_gw_records = [
            {
                "gateway_transaction_id": "GW_NORM_TEST_100",
                "payment_id": "pay_norm_100",
                "order_id": "ORD_NORM_100",
                "customer_id": "CUST_100",
                "amount": " ₹ 15,000.00 ",
                "currency": "INR",
                "payment_method": "NET_BANKING",
                "status": "captured",
                "transaction_date": "2026-08-12 11:00:00",
                "fee": "300.00",
                "tax": "54.00",
                "net_amount": "14646.00"
            }
        ]
        
        saved_txns = IngestionService.ingest_records(db, raw_gw_records, source="GATEWAY")
        assert len(saved_txns) == 1
        assert saved_txns[0].transaction_id == "GW_NORM_TEST_100"

        # Query back from SQLite
        db_record = db.query(Transaction).filter_by(transaction_id="GW_NORM_TEST_100").first()
        assert db_record is not None
        assert db_record.source == "GATEWAY"
        assert db_record.amount == 15000.0
        assert db_record.reference_id == "pay_norm_100"
        assert db_record.status == "CAPTURED"
    finally:
        db.close()
