"""
ReconcileAI - FastAPI Application Entrypoint (Phase 1 Baseline)
Provides health checks, system status, and foundation for multi-source reconciliation.
"""

import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, status, Depends, HTTPException, Request, Header
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from backend.config import settings
from backend.database import init_db, get_db
from backend.schemas.webhook import PaymentWebhookPayload, WebhookResponse
from backend.services.webhook import WebhookSimulatorService
from backend.services.security import verify_webhook_signature

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables on application startup
    init_db()
    yield

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=settings.DESCRIPTION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Enable CORS for local Streamlit frontend & testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health", status_code=status.HTTP_200_OK, tags=["System"])
def health_check():
    """
    Health check endpoint returning system status, version, and configuration state.
    """
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database_connected": bool(settings.DATABASE_URL),
        "ai_enabled": settings.AI_ENABLED,
        "llm_provider": settings.LLM_PROVIDER
    }

@app.get("/", tags=["System"])
def root():
    """
    Root endpoint with welcome message and API documentation links.
    """
    return {
        "message": "Welcome to ReconcileAI — Autonomous Multi-Source Payment Reconciliation System",
        "docs_url": "/docs",
        "health_url": "/health",
        "track": "Razorpay AI Buildathon — Track 04: AI Finance Controller"
    }

@app.post("/webhook/payment", response_model=WebhookResponse, status_code=status.HTTP_200_OK, tags=["Webhook Simulator"])
async def ingest_payment_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_razorpay_signature: Optional[str] = Header(None, alias="X-Razorpay-Signature")
):
    """
    Receives, cryptographically verifies HMAC signature, validates, and processes incoming payment gateway webhooks.
    Enforces idempotency, creates canonical Transaction records, and records immutable audit logs.
    """
    # 1. Read exact raw request body bytes for HMAC computation
    raw_body = await request.body()

    # 2. Extract signature header
    signature = x_razorpay_signature or request.headers.get("x-razorpay-signature")

    # 3. Parse JSON safely to extract event_id for failure auditing without trusting payload
    event_id = "UNKNOWN_EVENT"
    payload_dict = None
    if raw_body:
        try:
            payload_dict = json.loads(raw_body.decode("utf-8"))
            if isinstance(payload_dict, dict) and "event_id" in payload_dict:
                event_id = str(payload_dict["event_id"])
        except Exception:
            payload_dict = None

    # 4. Verify HMAC SHA-256 signature against settings.WEBHOOK_SECRET
    # If signature is not in header, check if it was provided in payload metadata
    sig_to_verify = signature or (payload_dict.get("signature") if isinstance(payload_dict, dict) else None)
    is_valid_sig = verify_webhook_signature(
        raw_body=raw_body,
        signature=sig_to_verify,
        secret=settings.WEBHOOK_SECRET
    )

    if not is_valid_sig:
        WebhookSimulatorService.record_signature_failure(
            db=db,
            entity_id=event_id,
            reason="Invalid or missing webhook signature"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing webhook signature"
        )

    # 5. Validate schema
    if payload_dict is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Malformed or invalid JSON payload"
        )

    try:
        payload = PaymentWebhookPayload(**payload_dict)
    except (ValueError, TypeError) as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e)
        )

    # 6. Process webhook (idempotency enforcement, transaction persistence, audit log)
    try:
        result = WebhookSimulatorService.process_webhook(
            db=db,
            payload_data=payload,
            signature=sig_to_verify
        )
        return result
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Webhook processing error: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=settings.API_HOST, port=settings.API_PORT, reload=True)

