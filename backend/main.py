"""
ReconcileAI - FastAPI Application Entrypoint (Phase 1 Baseline)
Provides health checks, system status, and foundation for multi-source reconciliation.
"""

import os
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, status, Depends, HTTPException, Request, Header, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from backend.config import settings
from backend.database import init_db, get_db

from backend.models.transaction import Transaction
from backend.models.reconciliation import ReconciliationResult
from backend.models.exception import ReconciliationException
from backend.models.audit import AuditLog

from backend.schemas.webhook import PaymentWebhookPayload, WebhookResponse
from backend.services.webhook import WebhookSimulatorService
from backend.services.security import verify_webhook_signature

from backend.schemas.exception import ExceptionActionRequest, ExceptionDetailResponse, ExceptionListResponse
from backend.services.exception_service import ExceptionManagementService

from backend.schemas.transaction import (
    TransactionResponse,
    TransactionListResponse,
    SyntheticLoadResponse,
)
from backend.schemas.reconciliation import (
    ReconciliationResultDetailResponse,
    ReconciliationResultListResponse,
    ReconciliationRunResponse,
)
from backend.schemas.audit import (
    AuditLogResponse,
    AuditLogListResponse,
)
from backend.schemas.report import (
    OperationalSummaryResponse,
)

from backend.services.ingestion import IngestionService
from backend.services.finance_controller import FinanceController
from backend.services.audit_service import AuditService
from backend.services.reconciliation import DeterministicReconciliationEngine

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

# -----------------------------------------------------------------------------
# Phase 11 — Exception Management Workflow Endpoints
# -----------------------------------------------------------------------------

@app.get("/exceptions", response_model=ExceptionListResponse, status_code=status.HTTP_200_OK, tags=["Exception Management"])
def list_exceptions(
    status: Optional[str] = None,
    severity: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """
    Retrieves a paginated list of reconciliation exceptions with optional status, severity, and category filtering.
    """
    items, total = ExceptionManagementService.list_exceptions(
        db=db,
        status_filter=status,
        severity_filter=severity,
        category_filter=category,
        limit=limit,
        offset=offset
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items
    }

@app.get("/exceptions/{exception_id}", response_model=ExceptionDetailResponse, status_code=status.HTTP_200_OK, tags=["Exception Management"])
def get_exception_detail(
    exception_id: str,
    db: Session = Depends(get_db)
):
    """
    Retrieves details for a single reconciliation exception.
    """
    exc = ExceptionManagementService.get_exception_by_id(db=db, exception_id=exception_id)
    if not exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Exception '{exception_id}' not found."
        )
    return exc

@app.post("/exceptions/{exception_id}/approve", response_model=ExceptionDetailResponse, status_code=status.HTTP_200_OK, tags=["Exception Management"])
def approve_exception(
    exception_id: str,
    action_req: Optional[ExceptionActionRequest] = None,
    db: Session = Depends(get_db)
):
    """
    Human reviewer approval decision for an exception.
    Sets status to APPROVED, updates linked reconciliation result, and records immutable audit log.
    """
    req = action_req or ExceptionActionRequest()
    return ExceptionManagementService.approve_exception(
        db=db,
        exception_id=exception_id,
        reviewer_id=req.reviewer_id,
        notes=req.notes
    )

@app.post("/exceptions/{exception_id}/reject", response_model=ExceptionDetailResponse, status_code=status.HTTP_200_OK, tags=["Exception Management"])
def reject_exception(
    exception_id: str,
    action_req: Optional[ExceptionActionRequest] = None,
    db: Session = Depends(get_db)
):
    """
    Human reviewer rejection decision for an exception.
    Sets status to REJECTED, updates linked reconciliation result, and records immutable audit log.
    """
    req = action_req or ExceptionActionRequest()
    return ExceptionManagementService.reject_exception(
        db=db,
        exception_id=exception_id,
        reviewer_id=req.reviewer_id,
        notes=req.notes
    )

# -----------------------------------------------------------------------------
# Phase 14 — Transaction Data Access & Ingestion Endpoints
# -----------------------------------------------------------------------------

@app.get("/transactions", response_model=TransactionListResponse, status_code=status.HTTP_200_OK, tags=["Transactions"])
def list_transactions(
    source: Optional[str] = Query(None, description="Filter by source (GATEWAY, BANK, ERP)"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by transaction status"),
    start_date: Optional[datetime] = Query(None, description="Filter transactions on or after this timestamp"),
    end_date: Optional[datetime] = Query(None, description="Filter transactions on or before this timestamp"),
    limit: int = Query(50, ge=1, le=500, description="Number of records to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: Session = Depends(get_db)
):
    """
    Retrieves a paginated list of canonical financial transactions with composable filters.
    """
    query = db.query(Transaction)
    if source:
        query = query.filter(Transaction.source == source.strip().upper())
    if status_filter:
        query = query.filter(Transaction.status == status_filter.strip().upper())
    if start_date:
        query = query.filter(Transaction.transaction_date >= start_date)
    if end_date:
        query = query.filter(Transaction.transaction_date <= end_date)

    total = query.count()
    items = query.order_by(Transaction.transaction_date.desc(), Transaction.id.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items
    }

@app.post("/transactions/load-synthetic", response_model=SyntheticLoadResponse, status_code=status.HTTP_200_OK, tags=["Transactions"])
def load_synthetic_transactions(
    data_dir: str = Query("data", description="Directory path containing synthetic transaction CSV files"),
    is_held_out: bool = Query(False, description="Whether to load held-out test datasets"),
    db: Session = Depends(get_db)
):
    """
    Loads and normalizes multi-source synthetic datasets (Gateway, Bank, ERP) from disk
    into the canonical transactions table using IngestionService. Built-in deduplication
    ensures records are safely updated if already present.
    """
    prefix = "held_out_" if is_held_out else ""
    gw_file = os.path.join(data_dir, f"{prefix}gateway_transactions.csv")
    bnk_file = os.path.join(data_dir, f"{prefix}bank_transactions.csv")
    erp_file = os.path.join(data_dir, f"{prefix}erp_transactions.csv")

    for fpath in (gw_file, bnk_file, erp_file):
        if not os.path.exists(fpath):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Synthetic dataset file not found at '{fpath}'. Please ensure data generation has been run."
            )

    try:
        gw_txns = IngestionService.ingest_csv_file(db=db, csv_path=gw_file, source="GATEWAY")
        bnk_txns = IngestionService.ingest_csv_file(db=db, csv_path=bnk_file, source="BANK")
        erp_txns = IngestionService.ingest_csv_file(db=db, csv_path=erp_file, source="ERP")
        total = len(gw_txns) + len(bnk_txns) + len(erp_txns)

        return {
            "status": "SUCCESS",
            "gateway_loaded": len(gw_txns),
            "bank_loaded": len(bnk_txns),
            "erp_loaded": len(erp_txns),
            "total_loaded": total,
            "message": f"Successfully ingested {total} transactions from '{data_dir}' (is_held_out={is_held_out})."
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to ingest synthetic data: {str(e)}"
        )

# -----------------------------------------------------------------------------
# Phase 14 — Reconciliation Execution & Results Endpoints
# -----------------------------------------------------------------------------

@app.post("/reconcile", response_model=ReconciliationRunResponse, status_code=status.HTTP_200_OK, tags=["Reconciliation"])
def trigger_reconciliation(
    db: Session = Depends(get_db)
):
    """
    Executes the multi-source financial reconciliation pipeline via FinanceController.
    Orchestrates deterministic matching, fuzzy investigation, and AI reasoning.
    Safely checks for already-reconciled records to prevent duplicate processing.
    """
    all_txns = db.query(Transaction).all()
    if not all_txns:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No transactions found in database to reconcile. Please load or ingest transactions first."
        )

    # Check already-reconciled transactions to enforce duplicate-run safety
    existing_results = db.query(ReconciliationResult).all()
    reconciled_ids = set()
    for r in existing_results:
        if r.gateway_transaction_id:
            reconciled_ids.add(r.gateway_transaction_id)
        if r.bank_transaction_id:
            reconciled_ids.add(r.bank_transaction_id)
        if r.erp_invoice_id:
            reconciled_ids.add(r.erp_invoice_id)

    # Cluster transactions to detect if any candidate cluster is pending reconciliation
    engine = DeterministicReconciliationEngine()
    clusters = engine.generate_candidate_clusters(all_txns)
    unreconciled_clusters = [
        c for c in clusters
        if not any(t.transaction_id in reconciled_ids for t in c["GATEWAY"] + c["BANK"] + c["ERP"])
    ]

    if existing_results and not unreconciled_clusters:
        # All candidate clusters have already been reconciled; skip to prevent duplicate results
        total_clusters = len(existing_results)
        auto_count = sum(1 for r in existing_results if r.final_decision == "AUTO_RECONCILED")
        review_count = sum(1 for r in existing_results if r.final_decision != "AUTO_RECONCILED")
        exceptions_count = db.query(ReconciliationException).count()
        auto_rate = round(auto_count / total_clusters * 100, 2) if total_clusters else 0.0
        open_exceptions = db.query(ReconciliationException).filter(ReconciliationException.status == "OPEN").all()
        var_inr = round(sum(e.difference_amount for e in open_exceptions), 2)

        return {
            "status": "SKIPPED",
            "total_clusters": total_clusters,
            "total_reconciled": auto_count,
            "total_review": review_count,
            "total_exceptions": exceptions_count,
            "auto_reconciled_rate": auto_rate,
            "unresolved_value_at_risk": var_inr,
            "message": f"All {len(all_txns)} staged transactions across {len(clusters)} candidate clusters have already been reconciled. No new transactions to process."
        }

    # Execute reconciliation via FinanceController (passes pending transactions or None if fresh run)
    controller = FinanceController(db=db)
    txns_to_process = [t for c in unreconciled_clusters for t in c["GATEWAY"] + c["BANK"] + c["ERP"]] if existing_results else None
    summary = controller.reconcile_and_investigate(transactions=txns_to_process, db=db, persist=True)

    # Re-query current totals from db
    all_results = db.query(ReconciliationResult).all()
    total_clusters = len(all_results)
    auto_count = sum(1 for r in all_results if r.final_decision == "AUTO_RECONCILED")
    review_count = sum(1 for r in all_results if r.final_decision != "AUTO_RECONCILED")
    exceptions_count = db.query(ReconciliationException).count()
    auto_rate = round(auto_count / total_clusters * 100, 2) if total_clusters else 0.0
    open_exceptions = db.query(ReconciliationException).filter(ReconciliationException.status == "OPEN").all()
    var_inr = round(sum(e.difference_amount for e in open_exceptions), 2)

    return {
        "status": "COMPLETED",
        "total_clusters": summary.get("total_clusters", total_clusters),
        "total_reconciled": summary.get("total_reconciled", auto_count),
        "total_review": summary.get("total_review", review_count),
        "total_exceptions": len(summary.get("exceptions", [])),
        "auto_reconciled_rate": auto_rate,
        "unresolved_value_at_risk": var_inr,
        "message": f"Reconciliation pipeline executed successfully. Processed {len(summary.get('results', []))} results."
    }

@app.get("/reconciliation/results", response_model=ReconciliationResultListResponse, status_code=status.HTTP_200_OK, tags=["Reconciliation"])
def list_reconciliation_results(
    final_decision: Optional[str] = Query(None, description="Filter by decision (AUTO_RECONCILED, HUMAN_REVIEW, etc.)"),
    is_resolved: Optional[bool] = Query(None, description="Filter by resolution status"),
    reconciliation_id: Optional[str] = Query(None, description="Filter by exact reconciliation ID"),
    limit: int = Query(50, ge=1, le=500, description="Number of records to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: Session = Depends(get_db)
):
    """
    Retrieves a paginated list of reconciliation results with composable filters.
    """
    query = db.query(ReconciliationResult)
    if final_decision:
        query = query.filter(ReconciliationResult.final_decision == final_decision.strip())
    if is_resolved is not None:
        query = query.filter(ReconciliationResult.is_resolved == is_resolved)
    if reconciliation_id:
        query = query.filter(ReconciliationResult.reconciliation_id == reconciliation_id.strip())

    total = query.count()
    items = query.order_by(ReconciliationResult.reconciled_at.desc(), ReconciliationResult.id.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items
    }

# -----------------------------------------------------------------------------
# Phase 14 — Immutable Audit Trail Query Endpoints (Read-Only)
# -----------------------------------------------------------------------------

@app.get("/audit", response_model=AuditLogListResponse, status_code=status.HTTP_200_OK, tags=["Audit Trail"])
def get_audit_trail(
    entity: Optional[str] = Query(None, description="Filter by entity category (RECONCILIATION, EXCEPTION, WEBHOOK, etc.)"),
    entity_id: Optional[str] = Query(None, description="Filter by specific entity ID"),
    action: Optional[str] = Query(None, description="Filter by action name"),
    limit: int = Query(100, ge=1, le=500, description="Maximum number of audit records to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: Session = Depends(get_db)
):
    """
    Queries immutable audit trail records with composable filters.
    Strictly read-only; AuditLog records cannot be modified or deleted.
    """
    query = db.query(AuditLog)
    if entity:
        query = query.filter(AuditLog.entity == entity.strip().upper())
    if entity_id:
        query = query.filter(AuditLog.entity_id == entity_id.strip())
    if action:
        query = query.filter(AuditLog.action == action.strip().upper())

    total = query.count()
    items = query.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items
    }

# -----------------------------------------------------------------------------
# Phase 14 — Operational Reporting & Metrics Endpoints
# -----------------------------------------------------------------------------

@app.get("/reports/summary", response_model=OperationalSummaryResponse, status_code=status.HTTP_200_OK, tags=["Reports"])
def get_operational_summary(
    db: Session = Depends(get_db)
):
    """
    Returns real-time operational summary metrics aggregated from current database state.
    Includes reconciliation rates, open exceptions, value at risk, and category breakdowns.
    """
    total_txns = db.query(Transaction).count()
    all_results = db.query(ReconciliationResult).all()
    total_results = len(all_results)
    auto_reconciled = sum(1 for r in all_results if r.final_decision == "AUTO_RECONCILED")

    all_exceptions = db.query(ReconciliationException).all()
    total_exceptions = len(all_exceptions)
    open_exceptions = [e for e in all_exceptions if e.status == "OPEN"]
    approved_exceptions = sum(1 for e in all_exceptions if e.status == "APPROVED")
    rejected_exceptions = sum(1 for e in all_exceptions if e.status == "REJECTED")

    auto_rate = round(auto_reconciled / total_results * 100, 2) if total_results else 0.0
    var_amount = round(sum(e.difference_amount for e in open_exceptions), 2)

    # Severity breakdown
    severity_counts: Dict[str, int] = {}
    for e in all_exceptions:
        severity_counts[e.severity] = severity_counts.get(e.severity, 0) + 1

    # Category breakdown
    category_counts: Dict[str, int] = {}
    for e in all_exceptions:
        category_counts[e.category] = category_counts.get(e.category, 0) + 1

    # SLA status breakdown
    sla_counts: Dict[str, int] = {}
    for e in all_exceptions:
        status_val = getattr(e, "sla_status", "OK")
        sla_counts[status_val] = sla_counts.get(status_val, 0) + 1

    return {
        "total_transactions": total_txns,
        "total_reconciliation_results": total_results,
        "total_auto_reconciled": auto_reconciled,
        "total_exceptions": total_exceptions,
        "open_exceptions": len(open_exceptions),
        "approved_exceptions": approved_exceptions,
        "rejected_exceptions": rejected_exceptions,
        "auto_reconciliation_rate": auto_rate,
        "unresolved_amount_inr": var_amount,
        "exceptions_by_severity": severity_counts,
        "exceptions_by_category": category_counts,
        "sla_status_breakdown": sla_counts
    }

# -----------------------------------------------------------------------------
# Phase 15 Extension — Benchmark Runner Endpoint
# -----------------------------------------------------------------------------

@app.post("/benchmark/run", status_code=status.HTTP_200_OK, tags=["Evaluation"])
def run_evaluation_benchmark(
    is_held_out: bool = Query(False, description="Run on held-out split (True) or primary ground truth (False)"),
    data_dir: str = Query("data", description="Directory containing benchmark CSV datasets")
):
    """
    Executes the non-mutating Phase 13 Reconciliation Benchmark against ground truth datasets.
    Evaluates classification metrics, operational statistics, financial Value-at-Risk, and throughput.
    """
    try:
        from evaluation.benchmark import ReconciliationBenchmark
        report = ReconciliationBenchmark.run_benchmark(data_dir=data_dir, is_held_out=is_held_out)
        return report.to_dict()
    except FileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Benchmark execution failed: {str(e)}"
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=settings.API_HOST, port=settings.API_PORT, reload=True)


