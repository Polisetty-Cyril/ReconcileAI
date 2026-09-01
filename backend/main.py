"""
ReconcileAI - FastAPI Application Entrypoint (Phase 1 Baseline)
Provides health checks, system status, and foundation for multi-source reconciliation.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, status, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from backend.config import settings
from backend.database import init_db, get_db
from backend.schemas.webhook import PaymentWebhookPayload, WebhookResponse
from backend.services.webhook import WebhookSimulatorService

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
def ingest_payment_webhook(
    payload: PaymentWebhookPayload,
    db: Session = Depends(get_db)
):
    """
    Receives, validates, and processes incoming payment gateway webhooks.
    Normalizes payload and persists WebhookEvent, canonical Transaction, and AuditLog.
    """
    try:
        result = WebhookSimulatorService.process_webhook(db=db, payload_data=payload)
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Webhook processing error: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=settings.API_HOST, port=settings.API_PORT, reload=True)

