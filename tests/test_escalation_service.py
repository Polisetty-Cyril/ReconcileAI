"""
Phase 12C-1 Unit Tests: Escalation State Machine Only
Verifies:
1. Ratio below 1.0 returns target level 0.
2. Exactly 1.0 returns target level 1.
3. 1.5 returns target level 1.
4. Exactly 2.0 returns target level 2.
5. 3.0 remains target level 2.
6. Current level 0 at 100%: transitions 0 -> 1.
7. Current level 1 at 200%: transitions 1 -> 2.
8. Current level 1 at 120%: no transition (remains 1).
9. Current level 2 at 300%: no transition (remains 2).
10. No downgrade is possible (monotonicity).
11. escalated_at is set only on an actual transition.
12. Repeated evaluation without transition does not change escalated_at.
13. APPROVED exception is ignored.
14. REJECTED exception is ignored.
15. RESOLVED exception is ignored.
16. status is never changed by escalation service.
17. is_resolved is never changed by escalation service.
18. final_decision is never changed by escalation service.
19. SLA fields (sla_duration_hours, sla_deadline, sla_status) are not changed by escalation service.
20. AI fields are not changed by escalation service.
21. Deterministic injected now works for time simulation.
22. Batch evaluation processes only OPEN exceptions.
"""

import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session

from backend.database import SessionLocal, init_db
from backend.models import ReconciliationException, ReconciliationResult
from backend.services.sla_service import SLAService
from backend.services.escalation_service import EscalationService, EscalationResult


@pytest.fixture(autouse=True)
def clean_test_escalation_records():
    """Initializes DB and cleans test records before and after each test."""
    init_db()
    db: Session = SessionLocal()
    try:
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like("%TEST_ESC_12C1%")).delete(synchronize_session=False)
        db.query(ReconciliationResult).filter(ReconciliationResult.reconciliation_id.like("%TEST_ESC_12C1%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like("%TEST_ESC_12C1%")).delete(synchronize_session=False)
        db.query(ReconciliationResult).filter(ReconciliationResult.reconciliation_id.like("%TEST_ESC_12C1%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


# =============================================================================
# TESTS 1-5: Target Escalation Level Calculation
# =============================================================================

def test_target_level_below_1_returns_level_0():
    """1. Ratio below 1.0 returns target level 0."""
    assert EscalationService.get_target_escalation_level(0.0) == 0
    assert EscalationService.get_target_escalation_level(0.5) == 0
    assert EscalationService.get_target_escalation_level(0.749) == 0
    assert EscalationService.get_target_escalation_level(0.75) == 0
    assert EscalationService.get_target_escalation_level(0.999) == 0


def test_target_level_exactly_1_returns_level_1():
    """2. Exactly 1.0 returns target level 1."""
    assert EscalationService.get_target_escalation_level(1.0) == 1


def test_target_level_1_point_5_returns_level_1():
    """3. 1.5 returns target level 1."""
    assert EscalationService.get_target_escalation_level(1.5) == 1
    assert EscalationService.get_target_escalation_level(1.999) == 1


def test_target_level_exactly_2_returns_level_2():
    """4. Exactly 2.0 returns target level 2."""
    assert EscalationService.get_target_escalation_level(2.0) == 2


def test_target_level_3_returns_level_2():
    """5. 3.0 remains target level 2 (never exceeds 2)."""
    assert EscalationService.get_target_escalation_level(3.0) == 2
    assert EscalationService.get_target_escalation_level(10.0) == 2


# =============================================================================
# TESTS 6-9: Monotonic State Transitions
# =============================================================================

def test_transition_0_to_1_at_100_percent():
    """6. Current level 0 at 100%: transition 0 -> 1."""
    created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    exc = ReconciliationException(
        exception_id="TEST_ESC_12C1_0_TO_1",
        transaction_id="TXN_TEST_ESC_01",
        category="AMOUNT_MISMATCH",
        severity="CRITICAL",  # 1 hour duration
        status="OPEN",
        created_at=created_at,
        escalation_level=0,
        escalated_at=None
    )

    # Exactly 100% elapsed: +1 hour
    now_100 = created_at + timedelta(hours=1.0)
    res = EscalationService.evaluate_exception(exc, now=now_100)

    assert res is not None
    assert res.previous_level == 0
    assert res.new_level == 1
    assert res.transitioned is True
    assert exc.escalation_level == 1
    assert exc.escalated_at == now_100


def test_transition_1_to_2_at_200_percent():
    """7. Current level 1 at 200%: transition 1 -> 2."""
    created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    first_escalated_at = created_at + timedelta(hours=4.0)
    exc = ReconciliationException(
        exception_id="TEST_ESC_12C1_1_TO_2",
        transaction_id="TXN_TEST_ESC_02",
        category="MISSING_BANK_TRANSACTION",
        severity="HIGH",  # 4 hours duration
        status="OPEN",
        created_at=created_at,
        escalation_level=1,
        escalated_at=first_escalated_at
    )

    # Exactly 200% elapsed: 4h * 2 = 8 hours
    now_200 = created_at + timedelta(hours=8.0)
    res = EscalationService.evaluate_exception(exc, now=now_200)

    assert res is not None
    assert res.previous_level == 1
    assert res.new_level == 2
    assert res.transitioned is True
    assert exc.escalation_level == 2
    assert exc.escalated_at == now_200


def test_current_level_1_at_120_percent_no_transition():
    """8. Current level 1 at 120%: no transition (remains 1)."""
    created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    first_escalated_at = created_at + timedelta(hours=4.0)
    exc = ReconciliationException(
        exception_id="TEST_ESC_12C1_STAY_1",
        transaction_id="TXN_TEST_ESC_03",
        category="AMOUNT_MISMATCH",
        severity="HIGH",  # 4 hours duration
        status="OPEN",
        created_at=created_at,
        escalation_level=1,
        escalated_at=first_escalated_at
    )

    # 120% elapsed: 4.8 hours
    now_120 = created_at + timedelta(hours=4.8)
    res = EscalationService.evaluate_exception(exc, now=now_120)

    assert res is not None
    assert res.previous_level == 1
    assert res.new_level == 1
    assert res.transitioned is False
    assert exc.escalation_level == 1
    # escalated_at must remain unchanged
    assert exc.escalated_at == first_escalated_at


def test_current_level_2_at_300_percent_no_transition():
    """9. Current level 2 at 300%: no transition (remains 2, never exceeds 2)."""
    created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    second_escalated_at = created_at + timedelta(hours=8.0)
    exc = ReconciliationException(
        exception_id="TEST_ESC_12C1_STAY_2",
        transaction_id="TXN_TEST_ESC_04",
        category="AMOUNT_MISMATCH",
        severity="HIGH",  # 4 hours duration
        status="OPEN",
        created_at=created_at,
        escalation_level=2,
        escalated_at=second_escalated_at
    )

    # 300% elapsed: 12.0 hours
    now_300 = created_at + timedelta(hours=12.0)
    res = EscalationService.evaluate_exception(exc, now=now_300)

    assert res is not None
    assert res.previous_level == 2
    assert res.new_level == 2
    assert res.transitioned is False
    assert exc.escalation_level == 2
    assert exc.escalated_at == second_escalated_at


# =============================================================================
# TEST 10: Monotonicity / No Downgrade
# =============================================================================

def test_no_downgrade_possible():
    """10. No downgrade is possible: levels 1 and 2 never decrease even if ratio < target."""
    created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    past_escalated = created_at + timedelta(hours=4.0)

    # Exception at Level 1 evaluated at ratio 0.5 (below 1.0)
    exc_lvl1 = ReconciliationException(
        exception_id="TEST_ESC_12C1_NO_DOWN_1",
        transaction_id="TXN_TEST_ESC_05",
        category="AMOUNT_MISMATCH",
        severity="HIGH",
        status="OPEN",
        created_at=created_at,
        escalation_level=1,
        escalated_at=past_escalated
    )
    now_50 = created_at + timedelta(hours=2.0)  # 50% of 4h
    res1 = EscalationService.evaluate_exception(exc_lvl1, now=now_50)
    assert res1.new_level == 1
    assert exc_lvl1.escalation_level == 1

    # Exception at Level 2 evaluated at ratio 1.5 (below 2.0)
    exc_lvl2 = ReconciliationException(
        exception_id="TEST_ESC_12C1_NO_DOWN_2",
        transaction_id="TXN_TEST_ESC_06",
        category="AMOUNT_MISMATCH",
        severity="HIGH",
        status="OPEN",
        created_at=created_at,
        escalation_level=2,
        escalated_at=past_escalated
    )
    now_150 = created_at + timedelta(hours=6.0)  # 150% of 4h
    res2 = EscalationService.evaluate_exception(exc_lvl2, now=now_150)
    assert res2.new_level == 2
    assert exc_lvl2.escalation_level == 2


# =============================================================================
# TESTS 11-12: escalated_at Timestamp Behavior
# =============================================================================

def test_escalated_at_set_only_on_actual_transition():
    """11. escalated_at is set only on an actual transition."""
    created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    exc = ReconciliationException(
        exception_id="TEST_ESC_12C1_TIME_SET",
        transaction_id="TXN_TEST_ESC_07",
        category="CRITICAL",
        severity="CRITICAL",
        status="OPEN",
        created_at=created_at,
        escalation_level=0,
        escalated_at=None
    )

    # 50% elapsed: no transition
    now_50 = created_at + timedelta(minutes=30)
    EscalationService.evaluate_exception(exc, now=now_50)
    assert exc.escalated_at is None

    # 100% elapsed: transition 0 -> 1
    now_100 = created_at + timedelta(hours=1.0)
    EscalationService.evaluate_exception(exc, now=now_100)
    assert exc.escalated_at == now_100


def test_repeated_evaluation_does_not_change_escalated_at():
    """12. Repeated evaluation without transition does not change escalated_at."""
    created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    original_escalated_at = created_at + timedelta(hours=1.0)

    exc = ReconciliationException(
        exception_id="TEST_ESC_12C1_TIME_STABLE",
        transaction_id="TXN_TEST_ESC_08",
        category="CRITICAL",
        severity="CRITICAL",
        status="OPEN",
        created_at=created_at,
        escalation_level=1,
        escalated_at=original_escalated_at
    )

    # Evaluate multiple times at 120%, 150%, 180%
    for elapsed_mins in [72, 90, 108]:
        now = created_at + timedelta(minutes=elapsed_mins)
        res = EscalationService.evaluate_exception(exc, now=now)
        assert res.transitioned is False
        assert exc.escalated_at == original_escalated_at


# =============================================================================
# TESTS 13-15: Non-OPEN Safety Tests
# =============================================================================

@pytest.mark.parametrize("non_open_status", ["APPROVED", "REJECTED", "RESOLVED"])
def test_non_open_exceptions_ignored_by_escalation(non_open_status):
    """13-15. APPROVED, REJECTED, and RESOLVED exceptions are ignored."""
    created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    exc = ReconciliationException(
        exception_id=f"TEST_ESC_12C1_{non_open_status}",
        transaction_id="TXN_TEST_ESC_09",
        category="AMOUNT_MISMATCH",
        severity="HIGH",
        status=non_open_status,
        created_at=created_at,
        escalation_level=0,
        escalated_at=None
    )

    # Massive 500% overrun
    now_500 = created_at + timedelta(hours=50.0)
    res = EscalationService.evaluate_exception(exc, now=now_500)

    assert res is None
    assert exc.escalation_level == 0
    assert exc.escalated_at is None
    assert exc.status == non_open_status


# =============================================================================
# TESTS 16-20: Immutability of Other Fields
# =============================================================================

def test_status_and_resolution_and_sla_fields_never_changed():
    """16-20. status, is_resolved, final_decision, SLA fields, and AI fields are not changed."""
    created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    deadline = created_at + timedelta(hours=4.0)

    recon = ReconciliationResult(
        reconciliation_id="TEST_ESC_12C1_REC_IMMUTABLE",
        match_score=60.0,
        matching_method="EXACT_RULE",
        ai_recommendation="REVIEW",
        ai_confidence=85.0,
        ai_reasoning="Advisory AI reasoning",
        final_decision="HUMAN_REVIEW",
        discrepancy_amount=500.0,
        is_resolved=False
    )
    exc = ReconciliationException(
        exception_id="TEST_ESC_12C1_EXC_IMMUTABLE",
        reconciliation_id="TEST_ESC_12C1_REC_IMMUTABLE",
        transaction_id="TXN_TEST_ESC_10",
        category="AMOUNT_MISMATCH",
        severity="HIGH",
        difference_amount=500.0,
        ai_explanation="Original AI explanation",
        status="OPEN",
        reviewer_notes=None,
        resolved_by=None,
        resolved_at=None,
        created_at=created_at,
        sla_duration_hours=4.0,
        sla_deadline=deadline,
        sla_status="BREACHED",
        escalation_level=0,
        escalated_at=None
    )

    now_150 = created_at + timedelta(hours=6.0)
    res = EscalationService.evaluate_exception(exc, now=now_150)

    assert res.transitioned is True
    assert exc.escalation_level == 1

    # Verify immutability of non-escalation fields
    assert exc.status == "OPEN"
    assert exc.sla_duration_hours == 4.0
    assert exc.sla_deadline == deadline
    assert exc.sla_status == "BREACHED"
    assert exc.resolved_by is None
    assert exc.resolved_at is None
    assert exc.reviewer_notes is None
    assert exc.ai_explanation == "Original AI explanation"
    assert exc.difference_amount == 500.0

    # ReconciliationResult completely untouched
    assert recon.is_resolved is False
    assert recon.final_decision == "HUMAN_REVIEW"
    assert recon.ai_recommendation == "REVIEW"
    assert recon.ai_confidence == 85.0
    assert recon.ai_reasoning == "Advisory AI reasoning"


# =============================================================================
# TEST 21: Deterministic Injected now Mechanism
# =============================================================================

def test_deterministic_injected_now():
    """21. Injected now simulates deterministic time progression."""
    created_at = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    exc = ReconciliationException(
        exception_id="TEST_ESC_12C1_INJECT_NOW",
        transaction_id="TXN_TEST_ESC_11",
        category="CRITICAL",
        severity="CRITICAL",  # 1 hour
        status="OPEN",
        created_at=created_at,
        escalation_level=0,
        escalated_at=None
    )

    t1 = created_at + timedelta(minutes=45)  # 75% -> level 0
    res1 = EscalationService.evaluate_exception(exc, now=t1)
    assert res1.new_level == 0
    assert res1.transitioned is False

    t2 = created_at + timedelta(minutes=65)  # 108% -> level 1
    res2 = EscalationService.evaluate_exception(exc, now=t2)
    assert res2.new_level == 1
    assert res2.transitioned is True
    assert exc.escalated_at == t2

    t3 = created_at + timedelta(minutes=130)  # 216% -> level 2
    res3 = EscalationService.evaluate_exception(exc, now=t3)
    assert res3.new_level == 2
    assert res3.transitioned is True
    assert exc.escalated_at == t3


# =============================================================================
# TEST 22: Batch Evaluation Processes Only OPEN Exceptions
# =============================================================================

def test_batch_evaluation_processes_only_open_exceptions():
    """22. Batch evaluation evaluates and updates only OPEN exceptions in DB."""
    db: Session = SessionLocal()
    try:
        base_time = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)

        exc_open_0_to_1 = ReconciliationException(
            exception_id="TEST_ESC_12C1_BATCH_OPEN_1",
            transaction_id="TXN_TEST_ESC_B1",
            category="AMOUNT_MISMATCH",
            severity="CRITICAL",  # 1h
            status="OPEN",
            created_at=base_time,
            escalation_level=0,
            escalated_at=None
        )
        exc_open_already_1 = ReconciliationException(
            exception_id="TEST_ESC_12C1_BATCH_OPEN_2",
            transaction_id="TXN_TEST_ESC_B2",
            category="AMOUNT_MISMATCH",
            severity="CRITICAL",  # 1h
            status="OPEN",
            created_at=base_time,
            escalation_level=1,
            escalated_at=base_time + timedelta(hours=1.0)
        )
        exc_approved = ReconciliationException(
            exception_id="TEST_ESC_12C1_BATCH_APPROVED",
            transaction_id="TXN_TEST_ESC_B3",
            category="AMOUNT_MISMATCH",
            severity="CRITICAL",
            status="APPROVED",
            created_at=base_time,
            escalation_level=0,
            escalated_at=None
        )

        db.add_all([exc_open_0_to_1, exc_open_already_1, exc_approved])
        db.commit()

        # Evaluate at base_time + 1.5h (150% elapsed)
        simulated_now = base_time + timedelta(hours=1.5)
        results = EscalationService.evaluate_all_open_exceptions(db, now=simulated_now)

        evaluated_ids = {r.exception_id for r in results}
        assert "TEST_ESC_12C1_BATCH_OPEN_1" in evaluated_ids
        assert "TEST_ESC_12C1_BATCH_OPEN_2" in evaluated_ids
        assert "TEST_ESC_12C1_BATCH_APPROVED" not in evaluated_ids

        db.refresh(exc_open_0_to_1)
        db.refresh(exc_open_already_1)
        db.refresh(exc_approved)

        # exc_open_0_to_1 transitioned 0 -> 1
        assert exc_open_0_to_1.escalation_level == 1
        assert exc_open_0_to_1.escalated_at is not None

        # exc_open_already_1 was already 1 and ratio is 1.5 (<2.0), so stayed 1
        assert exc_open_already_1.escalation_level == 1

        # exc_approved was never touched
        assert exc_approved.escalation_level == 0
        assert exc_approved.escalated_at is None
    finally:
        db.close()
