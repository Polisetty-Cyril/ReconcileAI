"""
ReconcileAI - Transaction Pydantic Schemas
Defines raw schemas for heterogeneous inputs and the unified CanonicalTransaction model.
"""

from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict

class CanonicalTransaction(BaseModel):
    """
    Standardized, clean financial transaction model.
    Every source (Payment Gateway, Bank Statement, ERP Ledger) is transformed into this canonical format.
    """
    model_config = ConfigDict(extra="ignore")

    transaction_id: str = Field(..., description="Unique source transaction or document ID (e.g. GW1001, BNK1001, INV1001)")
    source: str = Field(..., description="Originating financial system: GATEWAY, BANK, or ERP")
    reference_id: Optional[str] = Field(None, description="Reconciliation reference key (e.g. payment_id, UTR, or invoice ref)")
    order_id: Optional[str] = Field(None, description="E-commerce or system order identifier")
    customer_id: Optional[str] = Field(None, description="Customer or account identifier")
    amount: float = Field(..., description="Absolute monetary transaction value in INR")
    currency: str = Field("INR", description="Three-letter ISO currency code")
    transaction_date: datetime = Field(..., description="Parsed timezone-aware transaction or settlement datetime")
    status: str = Field(..., description="Standardized lifecycle status (e.g. CAPTURED, FAILED, PAID, CREDIT, DEBIT)")
    transaction_type: str = Field(..., description="Standardized type: PAYMENT, SETTLEMENT, INVOICE, REFUND")
    description: Optional[str] = Field(None, description="Narration string or transaction notes")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Source-specific extended metadata")

class GatewayRawInput(BaseModel):
    """Schema representing raw payment gateway inputs."""
    model_config = ConfigDict(extra="allow")

    gateway_transaction_id: Optional[str] = None
    payment_id: Optional[str] = None
    order_id: Optional[str] = None
    customer_id: Optional[str] = None
    amount: Any = None
    currency: Optional[str] = "INR"
    payment_method: Optional[str] = None
    status: Optional[str] = None
    transaction_date: Optional[Any] = None
    captured_at: Optional[Any] = None
    fee: Optional[Any] = 0.0
    tax: Optional[Any] = 0.0
    net_amount: Optional[Any] = None

class BankRawInput(BaseModel):
    """Schema representing raw bank statement entries."""
    model_config = ConfigDict(extra="allow")

    bank_transaction_id: Optional[str] = None
    bank_reference: Optional[str] = None
    transaction_date: Optional[Any] = None
    value_date: Optional[Any] = None
    description: Optional[str] = None
    credit_amount: Optional[Any] = 0.0
    debit_amount: Optional[Any] = 0.0
    balance: Optional[Any] = None
    bank_account: Optional[str] = None
    transaction_type: Optional[str] = None

class ERPRawInput(BaseModel):
    """Schema representing raw ERP accounting ledger records."""
    model_config = ConfigDict(extra="allow")

    invoice_id: Optional[str] = None
    order_id: Optional[str] = None
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    invoice_amount: Optional[Any] = None
    expected_payment: Optional[Any] = None
    invoice_date: Optional[Any] = None
    payment_status: Optional[str] = None
    reference_id: Optional[str] = None
