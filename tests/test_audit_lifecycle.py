"""
ReconcileAI - Financial Lifecycle Audit Event Wiring Tests (Phase 4C)
Verifies that all meaningful financial lifecycle events are logged via AuditService
into the immutable AuditLog:
A. Deterministic reconciliation produces RECONCILIATION_COMPLETED.
B. Exception creation produces EXCEPTION_CREATED.
C. Fuzzy investigation produces FUZZY_INVESTIGATED.
D. Exact AUTO_RECONCILED fast path does NOT produce FUZZY_INVESTIGATED.
E. AI reasoning produces exactly one AI_REASONED event.
F. Human approval produces EXCEPTION_APPROVED.
G. Human rejection produces EXCEPTION_REJECTED.
H. SLA warning produces SLA_WARNING exactly once for the transition.
I. SLA breach produces SLA_BREACHED exactly once for the transition.
J. Escalation 0 -> 1 produces ESCALATION_L1 exactly once.
K. Escalation 1 -> 2 produces ESCALATION_L2 exactly once.
L. Repeated orchestration without a new transition does not duplicate escalation/SLA audit events.
M. Notification dispatch produces NOTIFICATION_DISPATCHED.
N. Audit records are actually persisted and readable through AuditService.get_audit_trail().
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.orm import Session

from backend.database import SessionLocal, init_db
from backend.models.audit import AuditLog, audit_log_cleanup_context
from backend.models.exception import ReconciliationException
from backend.models.reconciliation import ReconciliationResult
from backend.models.transaction import Transaction
from backend.models.notification_log import NotificationLog
from backend.schemas.ai_controller import AIControllerResult
from backend.services.ai_controller import AIController
from backend.services.audit_service import AuditService
from backend.services.email_transport import MockEmailTransport
from backend.services.exception_service import ExceptionManagementService
from backend.services.finance_controller import FinanceController
from backend.services.fuzzy_matcher import FuzzyMatchEngine
from backend.services.llm_client import HeuristicLLMClient
from backend.services.notification_service import NotificationService
from backend.services.reconciliation import DeterministicReconciliationEngine
from backend.services.sla_orchestrator import SLAOrchestrator


# ---------------------------------------------------------------------------
# Fixtures & Isolation
# ---------------------------------------------------------------------------

def cleanup_4c_records(db: Session) -> None:
    """Purges all Phase 4C test records across all tables."""
    with audit_log_cleanup_context():
        db.query(AuditLog).filter(
            (AuditLog.entity_id.like("AUDIT_4C_%")) |
            (AuditLog.audit_id.like("AUD_AUDIT_4C_%"))
        ).delete(synchronize_session=False)
        db.query(NotificationLog).filter(NotificationLog.exception_id.like("AUDIT_4C_%")).delete(synchronize_session=False)
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like("AUDIT_4C_%")).delete(synchronize_session=False)
        db.query(ReconciliationResult).filter(ReconciliationResult.reconciliation_id.like("AUDIT_4C_%")).delete(synchronize_session=False)
        db.query(Transaction).filter(Transaction.transaction_id.like("AUDIT_4C_%")).delete(synchronize_session=False)
        db.commit()


@pytest.fixture(scope="module", autouse=True)
def setup_module_db():
    init_db()
    with SessionLocal() as db:
        cleanup_4c_records(db)
    yield
    with SessionLocal() as db:
        cleanup_4c_records(db)


@pytest.fixture
def db_session():
    with SessionLocal() as db:
        cleanup_4c_records(db)
        try:
            yield db
        finally:
            cleanup_4c_records(db)


# ---------------------------------------------------------------------------
# A & B. Reconciliation outcome and Exception creation
# ---------------------------------------------------------------------------

def test_reconciliation_and_exception_audit_events(db_session: Session):
    """
    Verifies that running the reconciliation pipeline logs:
    - RECONCILIATION_COMPLETED for each produced outcome
    - EXCEPTION_CREATED for each produced exception
    """
    engine = DeterministicReconciliationEngine()
    now = datetime.now(timezone.utc)

    # 1. Exact match pair (GW + Bank)
    t1 = Transaction(
        transaction_id="AUDIT_4C_TXN_GW_1",
        source="GATEWAY",
        reference_id="AUDIT_4C_REF_EXACT",
        amount=1000.0,
        currency="INR",
        transaction_date=now,
        status="SUCCESS",
        transaction_type="PAYMENT",
    )
    t2 = Transaction(
        transaction_id="AUDIT_4C_TXN_BNK_1",
        source="BANK",
        reference_id="AUDIT_4C_REF_EXACT",
        amount=1000.0,
        currency="INR",
        transaction_date=now,
        status="CREDIT",
        transaction_type="SETTLEMENT",
    )

    # 2. Discrepant transaction (Missing Bank, amount 2500)
    t3 = Transaction(
        transaction_id="AUDIT_4C_TXN_GW_2",
        source="GATEWAY",
        reference_id="AUDIT_4C_REF_DISCREPANT",
        amount=2500.0,
        currency="INR",
        transaction_date=now,
        status="SUCCESS",
        transaction_type="PAYMENT",
    )
    db_session.add_all([t1, t2, t3])
    db_session.commit()

    summary = engine.run_reconciliation_pipeline(db_session, [t1, t2, t3])
    assert len(summary["results"]) == 2
    assert len(summary["exceptions"]) == 1

    audit_svc = AuditService(db=db_session)

    # Verify RECONCILIATION_COMPLETED
    recon_audits = audit_svc.get_audit_trail(action="RECONCILIATION_COMPLETED")
    expected_ids = {r.gateway_transaction_id for r in summary["results"]} | {r.reconciliation_id for r in summary["results"]}
    relevant_recon = [a for a in recon_audits if a.entity_id in expected_ids]
    assert len(relevant_recon) == 2
    decisions = {a.new_value for a in relevant_recon}
    assert "AUTO_RECONCILED" in decisions
    assert ("HUMAN_REVIEW" in decisions or "EXCEPTION" in decisions)

    # Verify EXCEPTION_CREATED
    exc_audits = audit_svc.get_audit_trail(action="EXCEPTION_CREATED")
    relevant_exc = [a for a in exc_audits if a.entity_id == summary["exceptions"][0].exception_id]
    assert len(relevant_exc) == 1
    assert relevant_exc[0].entity == "EXCEPTION"
    exc_payload = json.loads(relevant_exc[0].new_value)
    assert exc_payload["status"] == "OPEN"
    assert exc_payload["severity"] == "HIGH"


# ---------------------------------------------------------------------------
# C & D. Fuzzy investigation vs. Exact AUTO_RECONCILED fast path
# ---------------------------------------------------------------------------

def test_fuzzy_investigation_logged_for_discrepancy(db_session: Session):
    """Verifies that an unresolved case with fuzzy evidence logs FUZZY_INVESTIGATED."""
    fc = FinanceController(db=db_session, ai_controller=AIController(client=HeuristicLLMClient()))
    audit_svc = AuditService(db=db_session)

    now = datetime.now(timezone.utc)
    gw = Transaction(
        transaction_id="AUDIT_4C_TXN_GW_FUZZY",
        source="GATEWAY",
        reference_id="AUDIT_4C_FUZZY_REF_A",
        amount=5000.0,
        currency="INR",
        transaction_date=now,
        status="SUCCESS",
        transaction_type="PAYMENT",
    )
    bnk = Transaction(
        transaction_id="AUDIT_4C_TXN_BNK_FUZZY",
        source="BANK",
        reference_id="AUDIT_4C_FUZZY_REF_B",  # Slight typo in reference
        amount=5000.0,
        currency="INR",
        transaction_date=now,
        status="CREDIT",
        transaction_type="SETTLEMENT",
    )
    db_session.add_all([gw, bnk])
    db_session.commit()

    recon_res = ReconciliationResult(
        reconciliation_id="AUDIT_4C_REC_FUZZY_1",
        gateway_transaction_id=gw.transaction_id,
        bank_transaction_id=bnk.transaction_id,
        match_score=60.0,
        matching_method="EXACT_RULE",
        final_decision="HUMAN_REVIEW",
        is_resolved=False,
    )
    db_session.add(recon_res)
    db_session.commit()

    # Investigate with AI & fuzzy and persist
    fc.investigate_with_ai(
        result=recon_res,
        gateway_txn=gw,
        bank_txn=bnk,
        persist=True,
        db=db_session,
    )
    db_session.commit()

    fuzzy_trail = audit_svc.get_audit_trail(action="FUZZY_INVESTIGATED", entity_id=gw.transaction_id)
    assert len(fuzzy_trail) == 1
    fuzzy_data = json.loads(fuzzy_trail[0].new_value)
    assert "composite_score" in fuzzy_data
    assert fuzzy_data["gateway_transaction_id"] == "AUDIT_4C_TXN_GW_FUZZY"

    ai_trail = audit_svc.get_audit_trail(action="AI_REASONED", entity_id=recon_res.reconciliation_id)
    assert len(ai_trail) == 1


def test_exact_fast_path_does_not_log_fuzzy_investigated(db_session: Session):
    """Verifies that AUTO_RECONCILED fast path NEVER logs FUZZY_INVESTIGATED."""
    fc = FinanceController(db=db_session, ai_controller=AIController(client=HeuristicLLMClient()))
    audit_svc = AuditService(db=db_session)

    recon_res = ReconciliationResult(
        reconciliation_id="AUDIT_4C_REC_EXACT_1",
        gateway_transaction_id="AUDIT_4C_TXN_GW_EXACT",
        bank_transaction_id="AUDIT_4C_TXN_BNK_EXACT",
        match_score=100.0,
        matching_method="EXACT_RULE",
        final_decision="AUTO_RECONCILED",
        is_resolved=True,
    )
    db_session.add(recon_res)
    db_session.commit()

    fc.investigate_with_ai(
        result=recon_res,
        persist=True,
        db=db_session,
    )
    db_session.commit()

    trail = audit_svc.get_audit_trail(entity_id=recon_res.reconciliation_id)
    actions = [a.action for a in trail]
    assert "FUZZY_INVESTIGATED" not in actions
    assert "AI_REASONED" in actions


# ---------------------------------------------------------------------------
# E. AI reasoning produces exactly one AI_REASONED event
# ---------------------------------------------------------------------------

def test_ai_reasoning_produces_single_event(db_session: Session):
    """Verifies AI reasoning produces exactly one advisory AI_REASONED event."""
    ai_ctrl = AIController(client=HeuristicLLMClient())
    audit_svc = AuditService(db=db_session)

    recon_res = ReconciliationResult(
        reconciliation_id="AUDIT_4C_REC_AI_1",
        gateway_transaction_id="AUDIT_4C_TXN_AI_1",
        match_score=70.0,
        matching_method="EXACT_RULE",
        final_decision="HUMAN_REVIEW",
        is_resolved=False,
    )
    db_session.add(recon_res)
    db_session.commit()

    ai_ctrl.investigate_and_persist(db=db_session, result=recon_res)
    db_session.commit()

    trail = audit_svc.get_audit_trail(entity_id=recon_res.reconciliation_id, action="AI_REASONED")
    assert len(trail) == 1
    assert trail[0].actor == "AI_CONTROLLER"
    assert trail[0].entity == "RECONCILIATION"
    # Verify advisory safety: result.is_resolved is still False
    assert recon_res.is_resolved is False


# ---------------------------------------------------------------------------
# F & G. Human Exception Approval & Rejection
# ---------------------------------------------------------------------------

def test_human_exception_approval_and_rejection(db_session: Session):
    """Verifies EXCEPTION_APPROVED and EXCEPTION_REJECTED are properly logged."""
    audit_svc = AuditService(db=db_session)
    now = datetime.now(timezone.utc)

    # 1. Approval flow
    exc1 = ReconciliationException(
        exception_id="AUDIT_4C_EXC_APP_1",
        reconciliation_id="AUDIT_4C_REC_APP_1",
        transaction_id="AUDIT_4C_TXN_APP_1",
        category="AMOUNT_MISMATCH",
        severity="MEDIUM",
        difference_amount=50.0,
        status="OPEN",
        created_at=now,
    )
    db_session.add(exc1)
    db_session.commit()

    ExceptionManagementService.approve_exception(
        db=db_session,
        exception_id="AUDIT_4C_EXC_APP_1",
        reviewer_id="HUMAN_REVIEWER_ALICE",
        notes="Approved settlement difference under policy."
    )

    trail_app = audit_svc.get_audit_trail(entity_id="AUDIT_4C_EXC_APP_1")
    assert len(trail_app) == 1
    assert trail_app[0].action == "EXCEPTION_APPROVED"
    assert trail_app[0].actor == "HUMAN_REVIEWER_ALICE"
    assert "Approved settlement difference" in trail_app[0].reason

    # 2. Rejection flow
    exc2 = ReconciliationException(
        exception_id="AUDIT_4C_EXC_REJ_1",
        reconciliation_id="AUDIT_4C_REC_REJ_1",
        transaction_id="AUDIT_4C_TXN_REJ_1",
        category="UNAUTHORIZED",
        severity="HIGH",
        difference_amount=2000.0,
        status="OPEN",
        created_at=now,
    )
    db_session.add(exc2)
    db_session.commit()

    ExceptionManagementService.reject_exception(
        db=db_session,
        exception_id="AUDIT_4C_EXC_REJ_1",
        reviewer_id="HUMAN_REVIEWER_BOB",
        notes="Rejected due to fraud indicator."
    )

    trail_rej = audit_svc.get_audit_trail(entity_id="AUDIT_4C_EXC_REJ_1")
    assert len(trail_rej) == 1
    assert trail_rej[0].action == "EXCEPTION_REJECTED"
    assert trail_rej[0].actor == "HUMAN_REVIEWER_BOB"


# ---------------------------------------------------------------------------
# H, I, J, K, L. SLA Warning, Breach, Escalation, and Idempotency
# ---------------------------------------------------------------------------

def test_sla_and_escalation_lifecycle_and_idempotency(db_session: Session):
    """
    Verifies that:
    - SLA_WARNING is logged exactly once on transition to WARNING
    - SLA_BREACHED is logged exactly once on transition to BREACHED
    - ESCALATION_L1 is logged exactly once on 0 -> 1 transition
    - ESCALATION_L2 is logged exactly once on 1 -> 2 transition
    - Repeated orchestration calls without transition emit NO duplicate audit events
    """
    audit_svc = AuditService(db=db_session)
    t0 = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)

    # HIGH severity has 4.0 hour SLA duration.
    # 80% (3.2 hrs) -> WARNING
    # 100% (4.0 hrs) -> BREACHED (and Escalation Level 1)
    # 200% (8.0 hrs) -> Escalation Level 2
    exc = ReconciliationException(
        exception_id="AUDIT_4C_EXC_SLA_1",
        reconciliation_id="AUDIT_4C_REC_SLA_1",
        transaction_id="AUDIT_4C_TXN_SLA_1",
        category="AMOUNT_MISMATCH",
        severity="HIGH",
        difference_amount=500.0,
        status="OPEN",
        sla_duration_hours=4.0,
        sla_status="OK",
        escalation_level=0,
        created_at=t0,
    )
    db_session.add(exc)
    db_session.commit()

    # Step 1: Advance to Warning threshold (3.5 hours, ratio = 0.875)
    t_warning = t0 + timedelta(hours=3.5)
    res_w1 = SLAOrchestrator.process_exception(db_session, exc, now=t_warning)
    assert res_w1.sla_status == "WARNING"

    trail_w1 = audit_svc.get_audit_trail(entity_id="AUDIT_4C_EXC_SLA_1")
    actions_w1 = [a.action for a in trail_w1]
    assert actions_w1 == ["SLA_WARNING"]

    # Step 2: Idempotency Check — repeated call at same warning time must NOT emit duplicate
    res_w2 = SLAOrchestrator.process_exception(db_session, exc, now=t_warning)
    trail_w2 = audit_svc.get_audit_trail(entity_id="AUDIT_4C_EXC_SLA_1")
    assert len(trail_w2) == 1  # Still exactly 1 SLA_WARNING!

    # Step 3: Advance to Breach & Level 1 (4.5 hours, ratio = 1.125)
    t_breach = t0 + timedelta(hours=4.5)
    res_b1 = SLAOrchestrator.process_exception(db_session, exc, now=t_breach)
    assert res_b1.sla_status == "BREACHED"
    assert res_b1.escalation_level == 1

    trail_b1 = audit_svc.get_audit_trail(entity_id="AUDIT_4C_EXC_SLA_1")
    actions_b1 = [a.action for a in trail_b1]
    assert actions_b1 == ["SLA_WARNING", "SLA_BREACHED", "ESCALATION_L1"]

    # Step 4: Idempotency Check — repeated call at breach time must NOT duplicate
    SLAOrchestrator.process_exception(db_session, exc, now=t_breach)
    trail_b2 = audit_svc.get_audit_trail(entity_id="AUDIT_4C_EXC_SLA_1")
    assert len(trail_b2) == 3

    # Step 5: Advance to Escalation Level 2 (8.5 hours, ratio = 2.125)
    t_l2 = t0 + timedelta(hours=8.5)
    res_l2 = SLAOrchestrator.process_exception(db_session, exc, now=t_l2)
    assert res_l2.escalation_level == 2

    trail_l2 = audit_svc.get_audit_trail(entity_id="AUDIT_4C_EXC_SLA_1")
    actions_l2 = [a.action for a in trail_l2]
    assert actions_l2 == ["SLA_WARNING", "SLA_BREACHED", "ESCALATION_L1", "ESCALATION_L2"]

    # Step 6: Idempotency Check — repeated call at L2 emits NO new audit events
    SLAOrchestrator.process_exception(db_session, exc, now=t_l2)
    trail_final = audit_svc.get_audit_trail(entity_id="AUDIT_4C_EXC_SLA_1")
    assert len(trail_final) == 4


# ---------------------------------------------------------------------------
# M. Notification dispatch produces NOTIFICATION_DISPATCHED
# ---------------------------------------------------------------------------

def test_notification_dispatch_audit_event(db_session: Session):
    """Verifies that delivering a notification logs NOTIFICATION_DISPATCHED."""
    audit_svc = AuditService(db=db_session)
    transport = MockEmailTransport(should_fail=False)

    now = datetime.now(timezone.utc)
    exc = ReconciliationException(
        exception_id="AUDIT_4C_EXC_NOTIF_1",
        reconciliation_id="AUDIT_4C_REC_NOTIF_1",
        transaction_id="AUDIT_4C_TXN_NOTIF_1",
        category="AMOUNT_MISMATCH",
        severity="HIGH",
        difference_amount=500.0,
        status="OPEN",
        sla_duration_hours=4.0,
        sla_status="WARNING",
        escalation_level=0,
        created_at=now,
    )
    db_session.add(exc)
    db_session.commit()

    notif_res = NotificationService.create_notification(
        db=db_session,
        exception=exc,
        event_type="SLA_WARNING",
        escalation_level=0,
        now=now,
    )
    assert notif_res.created is True

    # Deliver notification
    deliv_res = NotificationService.deliver_notification(
        db=db_session,
        notification_id=notif_res.notification_id,
        transport=transport,
        now=now,
    )
    assert deliv_res.delivery_success is True

    # Check audit log for NOTIFICATION_DISPATCHED
    trail = audit_svc.get_audit_trail(action="NOTIFICATION_DISPATCHED")
    relevant = [a for a in trail if a.entity_id == notif_res.notification_id]
    assert len(relevant) == 1
    assert relevant[0].entity == "NOTIFICATION"
    data = json.loads(relevant[0].new_value)
    assert data["exception_id"] == "AUDIT_4C_EXC_NOTIF_1"
    assert data["delivery_status"] == "SENT"
    assert data["recipient_email"] == "reviewer@reconcileai.local"


# ---------------------------------------------------------------------------
# N. Audit records persisted and readable through get_audit_trail
# ---------------------------------------------------------------------------

def test_audit_trail_retrieval_and_filtering(db_session: Session):
    """Verifies that all Phase 4C audit events can be queried with composable filters."""
    audit_svc = AuditService(db=db_session)

    # Log action through audit service
    entry = audit_svc.log_action(
        actor="SYSTEM",
        action="RECONCILIATION_COMPLETED",
        entity="RECONCILIATION",
        entity_id="AUDIT_4C_TEST_READ_1",
        new_value="AUTO_RECONCILED",
        reason="Test retrieval",
        commit=True,
    )

    # Query with entity_id filter
    trail = audit_svc.get_audit_trail(entity_id="AUDIT_4C_TEST_READ_1")
    assert len(trail) == 1
    assert trail[0].audit_id == entry.audit_id
    assert trail[0].new_value == "AUTO_RECONCILED"

    # Query with action filter
    trail_action = audit_svc.get_audit_trail(action="RECONCILIATION_COMPLETED", entity_id="AUDIT_4C_TEST_READ_1")
    assert len(trail_action) == 1
