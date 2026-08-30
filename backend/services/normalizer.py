"""
ReconcileAI - Data Normalization Layer
Transforms heterogeneous financial records from Payment Gateways, Bank Statements,
and ERP Ledgers into a unified CanonicalTransaction structure.
"""

import re
import math
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Union
from dateutil import parser as date_parser
from backend.schemas.transaction import (
    CanonicalTransaction,
    GatewayRawInput,
    BankRawInput,
    ERPRawInput
)

def parse_amount(val: Any) -> float:
    """
    Cleans and converts raw amount fields into a positive float.
    Handles currency symbols (₹, $, €), commas (,), whitespace, and string representations.
    """
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return 0.0
    if isinstance(val, (int, float)):
        return round(abs(float(val)), 2)
    
    # String cleaning
    str_val = str(val).strip()
    if not str_val or str_val.lower() in ("nan", "none", "null", ""):
        return 0.0
        
    # Remove currency symbols, commas, and trailing currency codes
    cleaned = re.sub(r"[^\d.-]", "", str_val)
    try:
        return round(abs(float(cleaned)), 2)
    except (ValueError, TypeError):
        return 0.0

def parse_datetime(val: Any) -> datetime:
    """
    Parses heterogeneous date strings into timezone-aware datetime objects.
    Supports ISO 8601, 'YYYY-MM-DD HH:MM:SS', 'YYYY-MM-DD', 'DD/MM/YYYY', 'DD-MM-YYYY'.
    Defaults to current UTC time if unparseable or missing.
    """
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return datetime.now(timezone.utc)
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val
        
    str_val = str(val).strip()
    if not str_val or str_val.lower() in ("nan", "none", "null", ""):
        return datetime.now(timezone.utc)

    # Common format parsing with dateutil
    try:
        parsed = date_parser.parse(str_val)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (ValueError, OverflowError, TypeError):
        return datetime.now(timezone.utc)

def clean_string(val: Any) -> Optional[str]:
    """Trims whitespace, replaces NaN/None with None."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    str_val = str(val).strip()
    return str_val if str_val and str_val.lower() not in ("nan", "none", "null", "") else None

def extract_reference_from_description(desc: Optional[str]) -> Optional[str]:
    """
    Extracts payment IDs (e.g. pay_1001), order IDs (ORD1001), or NEFT refs from bank descriptions.
    """
    if not desc:
        return None
    
    # 1. Search for payment ID pattern (e.g., pay_1001, HO_pay_5001)
    pay_match = re.search(r"\b(?:HO_)?pay_[A-Za-z0-9_]+\b", desc, re.IGNORECASE)
    if pay_match:
        return pay_match.group(0)

    # 2. Search for NEFT / UTR pattern
    neft_match = re.search(r"\b(?:HO_)?NEFT_[A-Za-z0-9_]+\b", desc, re.IGNORECASE)
    if neft_match:
        return neft_match.group(0)

    # 3. Search for Order ID pattern
    order_match = re.search(r"\b(?:HO_)?ORD[A-Za-z0-9_]+\b", desc, re.IGNORECASE)
    if order_match:
        return order_match.group(0)

    return None

class DataNormalizer:
    """Normalization service engine."""

    @staticmethod
    def normalize_gateway(raw: Union[Dict[str, Any], GatewayRawInput]) -> CanonicalTransaction:
        """Transforms a Payment Gateway record into CanonicalTransaction."""
        if isinstance(raw, GatewayRawInput):
            data = raw.model_dump()
        else:
            data = dict(raw)

        txn_id = clean_string(data.get("gateway_transaction_id")) or clean_string(data.get("payment_id")) or "GW_UNKNOWN"
        payment_id = clean_string(data.get("payment_id"))
        order_id = clean_string(data.get("order_id"))
        cust_id = clean_string(data.get("customer_id"))
        amount = parse_amount(data.get("amount"))
        currency = clean_string(data.get("currency")) or "INR"
        
        date_raw = data.get("captured_at") or data.get("transaction_date")
        txn_date = parse_datetime(date_raw)
        
        raw_status = clean_string(data.get("status")) or "CAPTURED"
        status = raw_status.upper()
        
        fee = parse_amount(data.get("fee"))
        tax = parse_amount(data.get("tax"))
        net_amount = parse_amount(data.get("net_amount")) or round(amount - fee - tax, 2)
        
        metadata = {
            "payment_method": clean_string(data.get("payment_method")),
            "fee": fee,
            "tax": tax,
            "net_amount": net_amount,
            "raw_status": raw_status
        }

        return CanonicalTransaction(
            transaction_id=txn_id,
            source="GATEWAY",
            reference_id=payment_id,
            order_id=order_id,
            customer_id=cust_id,
            amount=amount,
            currency=currency.upper(),
            transaction_date=txn_date,
            status=status,
            transaction_type="PAYMENT",
            description=f"Payment Gateway Charge {payment_id or txn_id}",
            metadata=metadata
        )

    @staticmethod
    def normalize_bank(raw: Union[Dict[str, Any], BankRawInput]) -> CanonicalTransaction:
        """Transforms a Bank Statement record into CanonicalTransaction."""
        if isinstance(raw, BankRawInput):
            data = raw.model_dump()
        else:
            data = dict(raw)

        txn_id = clean_string(data.get("bank_transaction_id")) or "BNK_UNKNOWN"
        bank_ref = clean_string(data.get("bank_reference"))
        desc = clean_string(data.get("description"))
        
        # If bank_reference is missing or generic, attempt extraction from description
        reference_id = bank_ref or extract_reference_from_description(desc)
        
        # Determine amount: check credit vs debit
        credit = parse_amount(data.get("credit_amount"))
        debit = parse_amount(data.get("debit_amount"))
        
        raw_type = clean_string(data.get("transaction_type")) or ("CREDIT" if credit > 0 else "DEBIT")
        txn_type = raw_type.upper()
        
        amount = credit if credit > 0 else debit
        
        date_raw = data.get("value_date") or data.get("transaction_date")
        txn_date = parse_datetime(date_raw)
        
        balance = parse_amount(data.get("balance"))
        bank_account = clean_string(data.get("bank_account"))

        metadata = {
            "credit_amount": credit,
            "debit_amount": debit,
            "balance": balance,
            "bank_account": bank_account,
            "raw_description": desc
        }

        return CanonicalTransaction(
            transaction_id=txn_id,
            source="BANK",
            reference_id=reference_id,
            order_id=extract_reference_from_description(desc) if not reference_id else None,
            customer_id=None,
            amount=amount,
            currency="INR",
            transaction_date=txn_date,
            status="CREDIT" if txn_type == "CREDIT" else "DEBIT",
            transaction_type="SETTLEMENT" if txn_type == "CREDIT" else "WITHDRAWAL",
            description=desc or f"Bank {txn_type} {txn_id}",
            metadata=metadata
        )

    @staticmethod
    def normalize_erp(raw: Union[Dict[str, Any], ERPRawInput]) -> CanonicalTransaction:
        """Transforms an ERP Ledger record into CanonicalTransaction."""
        if isinstance(raw, ERPRawInput):
            data = raw.model_dump()
        else:
            data = dict(raw)

        invoice_id = clean_string(data.get("invoice_id")) or "INV_UNKNOWN"
        order_id = clean_string(data.get("order_id"))
        cust_id = clean_string(data.get("customer_id"))
        cust_name = clean_string(data.get("customer_name"))
        ref_id = clean_string(data.get("reference_id"))
        
        invoice_amount = parse_amount(data.get("invoice_amount"))
        expected_payment = parse_amount(data.get("expected_payment")) or invoice_amount
        
        date_raw = data.get("invoice_date")
        txn_date = parse_datetime(date_raw)
        
        raw_status = clean_string(data.get("payment_status")) or "PAID"
        status = raw_status.upper()

        metadata = {
            "customer_name": cust_name,
            "expected_payment": expected_payment,
            "invoice_amount": invoice_amount,
            "raw_payment_status": raw_status
        }

        return CanonicalTransaction(
            transaction_id=invoice_id,
            source="ERP",
            reference_id=ref_id or order_id,
            order_id=order_id,
            customer_id=cust_id,
            amount=invoice_amount,
            currency="INR",
            transaction_date=txn_date,
            status=status,
            transaction_type="INVOICE",
            description=f"ERP Invoice {invoice_id} for {cust_name or cust_id}",
            metadata=metadata
        )

    @classmethod
    def normalize_record(cls, record: Dict[str, Any], source: str) -> CanonicalTransaction:
        """Dispatches to the source-specific normalization method."""
        source_upper = source.upper()
        if source_upper in ("GATEWAY", "PAYMENT_GATEWAY", "RAZORPAY"):
            return cls.normalize_gateway(record)
        elif source_upper in ("BANK", "BANK_STATEMENT", "STATEMENT"):
            return cls.normalize_bank(record)
        elif source_upper in ("ERP", "INVOICE", "LEDGER"):
            return cls.normalize_erp(record)
        else:
            raise ValueError(f"Unsupported transaction source: '{source}'")

    @classmethod
    def normalize_batch(cls, records: List[Dict[str, Any]], source: str) -> List[CanonicalTransaction]:
        """Normalizes a list of dictionary records for a given source."""
        return [cls.normalize_record(rec, source) for rec in records]
