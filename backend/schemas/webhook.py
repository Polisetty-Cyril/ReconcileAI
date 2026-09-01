"""
ReconcileAI - Payment Webhook Pydantic Schemas (Phase 9)
Defines payload schemas for simulated payment gateway webhooks and processing responses.
"""

from datetime import datetime
from typing import Optional, Dict, Any, Union
from pydantic import BaseModel, Field, ConfigDict, field_validator

SUPPORTED_WEBHOOK_EVENTS = {
    "payment.authorized",
    "payment.captured",
    "payment.failed",
    "refund.created"
}

class PaymentWebhookPayload(BaseModel):
    """
    Schema representing incoming payment gateway webhook payloads.
    Supported event types:
    - payment.authorized
    - payment.captured
    - payment.failed
    - refund.created
    """
    model_config = ConfigDict(extra="allow")

    event_id: str = Field(..., min_length=1, description="Unique webhook event identifier / idempotency key")
    event_type: str = Field(..., description="Gateway event type: payment.authorized, payment.captured, payment.failed, refund.created")
    payment_id: str = Field(..., min_length=1, description="Unique payment identifier (e.g. pay_1001)")
    order_id: Optional[str] = Field(None, description="Associated order identifier (e.g. ORD_1001)")
    customer_id: Optional[str] = Field(None, description="Customer account or profile identifier")
    amount: float = Field(..., description="Monetary transaction amount (positive float)")
    currency: str = Field("INR", description="Three-letter ISO currency code")
    status: Optional[str] = Field(None, description="Optional raw status override")
    payment_method: Optional[str] = Field(None, description="Method used: card, upi, netbanking, wallet")
    fee: Optional[float] = Field(0.0, description="Gateway processing fee")
    tax: Optional[float] = Field(0.0, description="Tax on processing fee")
    description: Optional[str] = Field(None, description="Transaction description or narration")
    timestamp: Optional[Union[datetime, str]] = Field(None, description="Event occurrence timestamp")
    signature: Optional[str] = Field(None, description="Gateway signature header/metadata")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Extended attributes")

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        clean_event = v.strip().lower() if isinstance(v, str) else ""
        if clean_event not in SUPPORTED_WEBHOOK_EVENTS:
            raise ValueError(
                f"Unsupported event_type '{v}'. Must be one of: {sorted(list(SUPPORTED_WEBHOOK_EVENTS))}"
            )
        return clean_event

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: float) -> float:
        if v is None or v <= 0:
            raise ValueError("Amount must be a positive number greater than zero.")
        return round(float(v), 2)

class WebhookResponse(BaseModel):
    """
    Schema for webhook ingestion endpoint responses.
    """
    model_config = ConfigDict(extra="ignore")

    status: str = Field("success", description="Status string: success or error")
    message: str = Field(..., description="Human-readable processing summary")
    event_id: str = Field(..., description="Processed event identifier")
    transaction_id: str = Field(..., description="Canonical gateway transaction identifier")
    event_type: str = Field(..., description="Processed webhook event type")
    processed: bool = Field(True, description="Whether event was successfully processed and persisted")
