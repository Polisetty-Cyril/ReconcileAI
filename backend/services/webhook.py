"""
ReconcileAI - Webhook Ingestion Service (Phase 9 & 10)
Processes incoming payment gateway webhook events, enforces idempotency, normalizes
payloads into canonical gateway transactions, persists webhook events and security audit logs.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Union
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.models.webhook import WebhookEvent
from backend.models.transaction import Transaction
from backend.models.audit import AuditLog
from backend.schemas.webhook import PaymentWebhookPayload, WebhookResponse
from backend.services.normalizer import parse_datetime, parse_amount, clean_string

# Event to Canonical Transaction Type and Status Mapping
EVENT_MAPPING = {
    "payment.authorized": {
        "transaction_type": "PAYMENT",
        "status": "AUTHORIZED"
    },
    "payment.captured": {
        "transaction_type": "PAYMENT",
        "status": "CAPTURED"
    },
    "payment.failed": {
        "transaction_type": "PAYMENT",
        "status": "FAILED"
    },
    "refund.created": {
        "transaction_type": "REFUND",
        "status": "REFUNDED"
    }
}

class WebhookSimulatorService:
    """Service handling simulated payment gateway webhook ingestion with security and idempotency."""

    @classmethod
    def record_signature_failure(
        cls,
        db: Session,
        entity_id: str = "UNKNOWN_EVENT",
        reason: str = "Invalid or missing webhook signature"
    ) -> AuditLog:
        """
        Creates an immutable audit log entry for a failed signature verification attempt.
        Does not trust unverified payload data as authoritative.
        """
        audit_entry = AuditLog(
            audit_id=f"AUD_WH_SIG_{uuid.uuid4().hex[:12].upper()}",
            timestamp=datetime.now(timezone.utc),
            actor="WEBHOOK_GATEWAY",
            action="WEBHOOK_SIGNATURE_FAILED",
            entity="WEBHOOK",
            entity_id=entity_id,
            old_value=None,
            new_value=json.dumps({"signature_verified": False, "rejected": True}),
            reason=reason
        )
        db.add(audit_entry)
        db.commit()
        db.refresh(audit_entry)
        return audit_entry

    @classmethod
    def record_duplicate_rejection(
        cls,
        db: Session,
        event_id: str,
        payment_id: Optional[str] = None,
        reason: str = "Duplicate webhook event_id rejected"
    ) -> AuditLog:
        """
        Creates an immutable audit log entry for a rejected duplicate webhook event.
        """
        audit_entry = AuditLog(
            audit_id=f"AUD_WH_DUP_{uuid.uuid4().hex[:12].upper()}",
            timestamp=datetime.now(timezone.utc),
            actor="WEBHOOK_GATEWAY",
            action="WEBHOOK_DUPLICATE_REJECTED",
            entity="WEBHOOK",
            entity_id=event_id,
            old_value=None,
            new_value=json.dumps({
                "event_id": event_id,
                "payment_id": payment_id,
                "rejected": True,
                "reason": reason
            }),
            reason=reason
        )
        db.add(audit_entry)
        db.commit()
        db.refresh(audit_entry)
        return audit_entry

    @classmethod
    def process_webhook(
        cls,
        db: Session,
        payload_data: Union[PaymentWebhookPayload, Dict[str, Any]],
        signature: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Validates, normalizes, and stores a gateway webhook event:
        1. Validates event type and payload structure
        2. Enforces idempotency (rejects previously processed event_id with HTTP 409)
        3. Persists WebhookEvent in webhook_events table
        4. Normalizes payload and persists canonical Transaction (source='GATEWAY')
        5. Creates AuditLog record (actor='WEBHOOK_GATEWAY', action='WEBHOOK_RECEIVED')
        6. Commits transactional state
        """
        # Ensure payload is a valid PaymentWebhookPayload schema instance
        if isinstance(payload_data, dict):
            payload = PaymentWebhookPayload(**payload_data)
        else:
            payload = payload_data

        event_type = payload.event_type.strip().lower()
        if event_type not in EVENT_MAPPING:
            raise ValueError(f"Unsupported event type: '{event_type}'")

        # 1. Idempotency Check: Verify if event_id has already been processed
        existing_event = db.query(WebhookEvent).filter_by(event_id=payload.event_id).first()
        if existing_event:
            cls.record_duplicate_rejection(
                db=db,
                event_id=payload.event_id,
                payment_id=payload.payment_id,
                reason=f"Duplicate webhook event_id '{payload.event_id}' rejected"
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Duplicate webhook event '{payload.event_id}' rejected. Event already processed."
            )

        mapping = EVENT_MAPPING[event_type]
        transaction_type = mapping["transaction_type"]
        status_name = mapping["status"]

        # Parse fields & timestamps
        amount = parse_amount(payload.amount)
        currency = (clean_string(payload.currency) or "INR").upper()
        txn_date = parse_datetime(payload.timestamp) if payload.timestamp else datetime.now(timezone.utc)
        
        fee = parse_amount(payload.fee) if payload.fee is not None else 0.0
        tax = parse_amount(payload.tax) if payload.tax is not None else 0.0
        net_amount = round(amount - fee - tax, 2) if transaction_type == "PAYMENT" else -amount

        metadata_dict = dict(payload.metadata) if payload.metadata else {}
        metadata_dict.update({
            "payment_method": clean_string(payload.payment_method),
            "fee": fee,
            "tax": tax,
            "net_amount": net_amount,
            "event_id": payload.event_id,
            "event_type": event_type,
            "raw_status": status_name
        })

        effective_signature = signature or payload.signature

        # 2. Persist WebhookEvent record
        payload_json_str = json.dumps(payload.model_dump(mode="json"))
        webhook_event = WebhookEvent(
            event_id=payload.event_id,
            event_type=event_type,
            payment_id=payload.payment_id,
            order_id=payload.order_id,
            amount=amount,
            currency=currency,
            signature=effective_signature,
            payload_json=payload_json_str,
            is_processed=True,
            received_at=datetime.now(timezone.utc)
        )
        db.add(webhook_event)

        # 3. Persist Canonical Gateway Transaction
        # Check if transaction already exists (update or insert)
        existing_txn = db.query(Transaction).filter_by(
            transaction_id=payload.payment_id,
            source="GATEWAY"
        ).first()

        desc = payload.description or f"Payment Gateway {transaction_type} {payload.payment_id} ({event_type})"

        if existing_txn:
            existing_txn.reference_id = payload.payment_id
            existing_txn.order_id = payload.order_id or existing_txn.order_id
            existing_txn.customer_id = payload.customer_id or existing_txn.customer_id
            existing_txn.amount = amount
            existing_txn.currency = currency
            existing_txn.transaction_date = txn_date
            existing_txn.status = status_name
            existing_txn.transaction_type = transaction_type
            existing_txn.description = desc
            existing_txn.metadata_json = json.dumps(metadata_dict)
            txn_record = existing_txn
        else:
            txn_record = Transaction(
                transaction_id=payload.payment_id,
                source="GATEWAY",
                reference_id=payload.payment_id,
                order_id=payload.order_id,
                customer_id=payload.customer_id,
                amount=amount,
                currency=currency,
                transaction_date=txn_date,
                status=status_name,
                transaction_type=transaction_type,
                description=desc,
                metadata_json=json.dumps(metadata_dict)
            )
            db.add(txn_record)

        # 4. Persist AuditLog entry
        audit_entry = AuditLog(
            audit_id=f"AUD_WH_{uuid.uuid4().hex[:12].upper()}",
            timestamp=datetime.now(timezone.utc),
            actor="WEBHOOK_GATEWAY",
            action="WEBHOOK_RECEIVED",
            entity="WEBHOOK",
            entity_id=payload.event_id,
            old_value=None,
            new_value=json.dumps({
                "event_id": payload.event_id,
                "event_type": event_type,
                "payment_id": payload.payment_id,
                "amount": amount,
                "status": status_name,
                "transaction_type": transaction_type
            }),
            reason=f"Processed webhook {event_type} for payment {payload.payment_id}"
        )
        db.add(audit_entry)

        # Commit all entities atomically
        db.commit()
        db.refresh(webhook_event)
        db.refresh(txn_record)
        db.refresh(audit_entry)

        return {
            "status": "success",
            "message": f"Webhook event '{event_type}' ingested and canonical transaction '{payload.payment_id}' persisted.",
            "event_id": payload.event_id,
            "transaction_id": txn_record.transaction_id,
            "event_type": event_type,
            "processed": True
        }
