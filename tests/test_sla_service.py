"""
Phase 12B Unit Tests: Deterministic SLA Engine
Verifies:
1. Duration mapping (CRITICAL=1h, HIGH=4h, MEDIUM=24h, LOW=48h, unknown/invalid fallback).
2. Deadline calculation (created_at + duration).
3. Status threshold boundaries (<75% OK, 75%-<100% WARNING, >=100% BREACHED, 200% BREACHED).
4. OPEN exception evaluation and field updates.
5. Non-OPEN safety: APPROVED, REJECTED, RESOLVED exceptions are strictly ignored.
6. Escalation safety: escalation_level and escalated_at are NEVER altered.
7. Resolution safety: status, is_resolved, final_decision are NEVER altered.
8. Timezone handling: supports both naive SQLite datetimes and timezone-aware UTC datetimes.
9. Existing Phase 12A compatibility: establishes authoritative duration (e.g. 24h default corrected to 4h for HIGH).
10. Database batch evaluation: evaluate_all_open_exceptions processes all open records atomically.
"""

import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session

from backend.database import SessionLocal, init_db
from backend.models import ReconciliationException, ReconciliationResult
from backend.services.sla_service import (
    SLAService,
    SLAEvaluationResult,
    SLA_SEVERITY_DURATIONS,
    DEFAULT_SLA_DURATION_HOURS
)

@pytest.fixture(autouse=True)
def clean_test_exceptions():
    """Initializes DB and cleans up any test records before and after each test."""
    init_db()
    db: Session = SessionLocal()
    try:
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like("%TEST_SLA_12B%")).delete(synchronize_session=False)
        db.query(ReconciliationResult).filter(ReconciliationResult.reconciliation_id.like("%TEST_SLA_12B%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(ReconciliationException).filter(ReconciliationException.exception_id.like("%TEST_SLA_12B%")).delete(synchronize_session=False)
        db.query(ReconciliationResult).filter(ReconciliationResult.reconciliation_id.like("%TEST_SLA_12B%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


# =============================================================================
# PART A: Duration Mapping Tests
# =============================================================================

def test_sla_duration_mapping():
    """Verify single source of truth for severity SLA durations and safe fallbacks."""
    assert SLAService.get_sla_duration("CRITICAL") == 1.0
    assert SLAService.get_sla_duration("HIGH") == 4.0
    assert SLAService.get_sla_duration("MEDIUM") == 24.0
    assert SLAService.get_sla_duration("LOW") == 48.0

    # Case-insensitivity and whitespace trimming
    assert SLAService.get_sla_duration("critical") == 1.0
    assert SLAService.get_sla_duration("  high  ") == 4.0
    assert SLAService.get_sla_duration("Medium") == 24.0
    assert SLAService.get_sla_duration("low") == 48.0

    # Explicit handling of unknown, empty, or None severity -> safe default (24.0 hours)
    assert SLAService.get_sla_duration("UNKNOWN_SEV") == DEFAULT_SLA_DURATION_HOURS
    assert SLAService.get_sla_duration("") == DEFAULT_SLA_DURATION_HOURS
    assert SLAService.get_sla_duration(None) == DEFAULT_SLA_DURATION_HOURS


# =============================================================================
# PART B: Deadline Calculation Tests
# =============================================================================

def test_sla_deadline_calculation():
    """Verify exact deadline calculation (created_at + duration) for each severity."""
    base_time = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)

    # CRITICAL: +1 hour
    d_crit, dead_crit, _, _ = SLAService.calculate_sla_state(base_time, "CRITICAL", now=base_time)
    assert d_crit == 1.0
    assert dead_crit == base_time + timedelta(hours=1.0)

    # HIGH: +4 hours
    d_high, dead_high, _, _ = SLAService.calculate_sla_state(base_time, "HIGH", now=base_time)
    assert d_high == 4.0
    assert dead_high == base_time + timedelta(hours=4.0)

    # MEDIUM: +24 hours
    d_med, dead_med, _, _ = SLAService.calculate_sla_state(base_time, "MEDIUM", now=base_time)
    assert d_med == 24.0
    assert dead_med == base_time + timedelta(hours=24.0)

    # LOW: +48 hours
    d_low, dead_low, _, _ = SLAService.calculate_sla_state(base_time, "LOW", now=base_time)
    assert d_low == 48.0
    assert dead_low == base_time + timedelta(hours=48.0)


# =============================================================================
# PART C: Status Boundaries Tests
# =============================================================================

def test_sla_status_boundaries():
    """
    Verify exact threshold boundaries:
    - 74.9% -> OK
    - 75.0% -> WARNING
    - 99.9% -> WARNING
    - 100.0% -> BREACHED
    - 200.0% -> BREACHED (stays BREACHED in Phase 12B, no escalation)
    """
    created_at = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    # Using HIGH severity: duration = 4.0 hours = 14,400 seconds
    total_seconds = 4.0 * 3600.0

    # 1. 74.9% elapsed: 14400 * 0.749 = 10785.6 seconds -> OK
    now_749 = created_at + timedelta(seconds=total_seconds * 0.749)
    _, _, ratio_749, status_749 = SLAService.calculate_sla_state(created_at, "HIGH", now=now_749)
    assert round(ratio_749, 4) == 0.749
    assert status_749 == "OK"

    # 2. Exactly 75.0% elapsed: 14400 * 0.75 = 10800 seconds -> WARNING
    now_750 = created_at + timedelta(seconds=total_seconds * 0.750)
    _, _, ratio_750, status_750 = SLAService.calculate_sla_state(created_at, "HIGH", now=now_750)
    assert round(ratio_750, 4) == 0.75
    assert status_750 == "WARNING"

    # 3. 99.9% elapsed: 14400 * 0.999 = 14385.6 seconds -> WARNING
    now_999 = created_at + timedelta(seconds=total_seconds * 0.999)
    _, _, ratio_999, status_999 = SLAService.calculate_sla_state(created_at, "HIGH", now=now_999)
    assert round(ratio_999, 4) == 0.999
    assert status_999 == "WARNING"

    # 4. Exactly 100.0% elapsed: 14400 seconds -> BREACHED
    now_1000 = created_at + timedelta(seconds=total_seconds * 1.000)
    _, _, ratio_1000, status_1000 = SLAService.calculate_sla_state(created_at, "HIGH", now=now_1000)
    assert round(ratio_1000, 4) == 1.0
    assert status_1000 == "BREACHED"

    # 5. 200.0% elapsed: 28800 seconds -> BREACHED (No escalation in Phase 12B)
    now_2000 = created_at + timedelta(seconds=total_seconds * 2.000)
    _, _, ratio_2000, status_2000 = SLAService.calculate_sla_state(created_at, "HIGH", now=now_2000)
    assert round(ratio_2000, 4) == 2.0
    assert status_2000 == "BREACHED"


# =============================================================================
# PART D: OPEN Exception Evaluation Tests
# =============================================================================

def test_evaluate_open_exception_updates_only_sla_fields():
    """Verify that evaluate_exception updates sla_duration_hours, sla_deadline, sla_status on an OPEN exception."""
    db: Session = SessionLocal()
    try:
        created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        exc = ReconciliationException(
            exception_id="TEST_SLA_12B_OPEN_01",
            reconciliation_id="REC_TEST_SLA_12B_01",
            transaction_id="TXN_TEST_SLA_12B_01",
            category="AMOUNT_MISMATCH",
            severity="CRITICAL",
            difference_amount=1000.0,
            status="OPEN",
            created_at=created_at,
            escalation_level=0,
            escalated_at=None
        )
        db.add(exc)
        db.commit()
        db.refresh(exc)

        # Simulate time at 80% of CRITICAL (1 hour SLA = 48 minutes elapsed)
        simulated_now = created_at + timedelta(minutes=48)
        result = SLAService.evaluate_exception(exc, now=simulated_now)

        assert result is not None
        assert result.exception_id == "TEST_SLA_12B_OPEN_01"
        assert result.severity == "CRITICAL"
        assert result.sla_duration_hours == 1.0
        assert result.sla_deadline == created_at + timedelta(hours=1.0)
        assert result.elapsed_ratio == 0.8
        assert result.sla_status == "WARNING"

        # Verify attributes on model object
        assert exc.sla_duration_hours == 1.0
        assert exc.sla_deadline == created_at + timedelta(hours=1.0)
        assert exc.sla_status == "WARNING"
    finally:
        db.close()


# =============================================================================
# PART E: Non-OPEN Safety Tests
# =============================================================================

@pytest.mark.parametrize("non_open_status", ["APPROVED", "REJECTED", "RESOLVED"])
def test_non_open_exceptions_completely_ignored(non_open_status):
    """Verify APPROVED, REJECTED, and RESOLVED exceptions are skipped and untouched."""
    db: Session = SessionLocal()
    try:
        created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        resolved_at = datetime(2026, 9, 1, 10, 30, 0, tzinfo=timezone.utc)
        initial_deadline = created_at + timedelta(hours=24.0)

        exc = ReconciliationException(
            exception_id=f"TEST_SLA_12B_{non_open_status}_01",
            reconciliation_id=f"REC_TEST_SLA_12B_{non_open_status}",
            transaction_id="TXN_TEST_SLA_12B_NON_OPEN",
            category="AMOUNT_MISMATCH",
            severity="HIGH",
            difference_amount=200.0,
            status=non_open_status,
            resolved_by="human_reviewer",
            resolved_at=resolved_at,
            reviewer_notes="Historical decision",
            created_at=created_at,
            sla_duration_hours=24.0,
            sla_deadline=initial_deadline,
            sla_status="OK",
            escalation_level=0,
            escalated_at=None
        )
        db.add(exc)
        db.commit()
        db.refresh(exc)

        # Simulate time 500 hours later (massively breached if it were open)
        simulated_now = created_at + timedelta(hours=500.0)

        result = SLAService.evaluate_exception(exc, now=simulated_now)
        assert result is None, f"Expected None for status='{non_open_status}', got {result}"

        # Confirm model fields are completely untouched
        assert exc.status == non_open_status
        assert exc.sla_status == "OK"  # NOT changed to BREACHED
        assert exc.sla_duration_hours == 24.0  # NOT changed
        assert SLAService.normalize_utc_datetime(exc.sla_deadline) == initial_deadline
        assert exc.resolved_by == "human_reviewer"
        assert SLAService.normalize_utc_datetime(exc.resolved_at) == resolved_at
        assert exc.reviewer_notes == "Historical decision"
    finally:
        db.close()


# =============================================================================
# PART F: Escalation Safety Tests
# =============================================================================

def test_escalation_fields_never_modified_by_phase12b():
    """Verify that escalation_level and escalated_at are NEVER modified by Phase 12B."""
    created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    exc = ReconciliationException(
        exception_id="TEST_SLA_12B_ESCALATION_SAFE",
        transaction_id="TXN_TEST_SLA_12B_ESC",
        category="MISSING_BANK_TRANSACTION",
        severity="HIGH",
        status="OPEN",
        created_at=created_at,
        escalation_level=0,
        escalated_at=None
    )

    # Simulate 300% of SLA elapsed (massively breached)
    now_300 = created_at + timedelta(hours=12.0)  # HIGH = 4h, 12h = 300%
    result = SLAService.evaluate_exception(exc, now=now_300)

    assert result is not None
    assert result.sla_status == "BREACHED"
    assert exc.sla_status == "BREACHED"

    # Critical Phase 12B safety invariant:
    assert exc.escalation_level == 0
    assert exc.escalated_at is None


# =============================================================================
# PART G: Financial & Resolution Safety Tests
# =============================================================================

def test_resolution_and_financial_fields_never_modified():
    """Verify that status, is_resolved, and final_decision are never touched by SLA engine."""
    db: Session = SessionLocal()
    try:
        created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        recon = ReconciliationResult(
            reconciliation_id="TEST_SLA_12B_RECON_FIN_01",
            match_score=50.0,
            matching_method="EXACT_RULE",
            final_decision="HUMAN_REVIEW",
            discrepancy_amount=750.0,
            is_resolved=False
        )
        exc = ReconciliationException(
            exception_id="TEST_SLA_12B_EXC_FIN_01",
            reconciliation_id="TEST_SLA_12B_RECON_FIN_01",
            transaction_id="TXN_TEST_SLA_12B_FIN",
            category="AMOUNT_MISMATCH",
            severity="CRITICAL",
            difference_amount=750.0,
            status="OPEN",
            created_at=created_at
        )
        db.add(recon)
        db.add(exc)
        db.commit()

        # Evaluate at 200% SLA elapsed
        simulated_now = created_at + timedelta(hours=2.0)
        SLAService.evaluate_all_open_exceptions(db, now=simulated_now)

        db.refresh(recon)
        db.refresh(exc)

        # Financial invariants preserved
        assert exc.status == "OPEN"
        assert exc.resolved_by is None
        assert exc.resolved_at is None
        assert recon.is_resolved is False
        assert recon.final_decision == "HUMAN_REVIEW"
        assert recon.discrepancy_amount == 750.0
    finally:
        db.close()


# =============================================================================
# PART H: Timezone Handling Tests
# =============================================================================

def test_timezone_naive_and_aware_datetimes():
    """
    Verify timezone normalization works cleanly with SQLite naive datetimes
    and explicit timezone-aware datetimes without raising TypeError.
    """
    # Naive created_at (common from SQLite reads)
    naive_created_at = datetime(2026, 9, 1, 12, 0, 0)
    # Aware now
    aware_now = datetime(2026, 9, 1, 14, 0, 0, tzinfo=timezone.utc)

    duration, deadline, ratio, status = SLAService.calculate_sla_state(
        created_at=naive_created_at,
        severity="HIGH",
        now=aware_now
    )
    assert duration == 4.0
    assert deadline.tzinfo == timezone.utc
    assert round(ratio, 4) == 0.5
    assert status == "OK"

    # Both naive
    naive_now = datetime(2026, 9, 1, 15, 0, 0)
    _, _, ratio_naive, status_naive = SLAService.calculate_sla_state(
        created_at=naive_created_at,
        severity="HIGH",
        now=naive_now
    )
    assert round(ratio_naive, 4) == 0.75
    assert status_naive == "WARNING"


# =============================================================================
# PART I: Existing Phase 12A Compatibility Tests
# =============================================================================

def test_phase12a_compatibility_corrects_default_duration():
    """
    Verify an OPEN HIGH exception with the Phase 12A default 24-hour SLA
    is corrected to the authoritative 4-hour SLA when evaluated.
    """
    created_at = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    # Created with Phase 12A default 24h duration
    exc = ReconciliationException(
        exception_id="TEST_SLA_12B_CORRECT_01",
        transaction_id="TXN_TEST_SLA_12B_CORRECT",
        category="AMOUNT_MISMATCH",
        severity="HIGH",
        status="OPEN",
        created_at=created_at,
        sla_duration_hours=24.0,
        sla_deadline=created_at + timedelta(hours=24.0),
        sla_status="OK"
    )

    # 3.5 hours later:
    # Under 24h SLA, 3.5h / 24h = 14.5% -> OK
    # Under authoritative 4h SLA, 3.5h / 4h = 87.5% -> WARNING
    now = created_at + timedelta(hours=3.5)

    result = SLAService.evaluate_exception(exc, now=now)
    assert result is not None

    # Corrected to authoritative severity duration
    assert exc.sla_duration_hours == 4.0
    assert exc.sla_deadline == created_at + timedelta(hours=4.0)
    assert exc.sla_status == "WARNING"
    assert result.elapsed_ratio == 0.875


# =============================================================================
# PART J: Batch Evaluation Tests
# =============================================================================

def test_evaluate_all_open_exceptions_batch():
    """Verify evaluate_all_open_exceptions processes and commits all OPEN records."""
    db: Session = SessionLocal()
    try:
        base_time = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)

        # Exc 1: CRITICAL created 30 min ago (50% -> OK)
        exc1 = ReconciliationException(
            exception_id="TEST_SLA_12B_BATCH_01",
            transaction_id="TXN_TEST_SLA_12B_B1",
            category="AMOUNT_MISMATCH",
            severity="CRITICAL",
            status="OPEN",
            created_at=base_time
        )
        # Exc 2: HIGH created 3.5 hours ago (87.5% -> WARNING)
        exc2 = ReconciliationException(
            exception_id="TEST_SLA_12B_BATCH_02",
            transaction_id="TXN_TEST_SLA_12B_B2",
            category="MISSING_BANK_TRANSACTION",
            severity="HIGH",
            status="OPEN",
            created_at=base_time - timedelta(hours=3.0)
        )
        # Exc 3: APPROVED (must be ignored)
        exc3 = ReconciliationException(
            exception_id="TEST_SLA_12B_BATCH_03",
            transaction_id="TXN_TEST_SLA_12B_B3",
            category="DUPLICATE_TRANSACTION",
            severity="CRITICAL",
            status="APPROVED",
            created_at=base_time - timedelta(hours=10.0),
            sla_status="OK"
        )
        db.add_all([exc1, exc2, exc3])
        db.commit()

        # Evaluate at base_time + 30 min
        now = base_time + timedelta(minutes=30)
        results = SLAService.evaluate_all_open_exceptions(db, now=now)

        # Only the 2 OPEN exceptions should be evaluated
        evaluated_ids = {r.exception_id for r in results}
        assert "TEST_SLA_12B_BATCH_01" in evaluated_ids
        assert "TEST_SLA_12B_BATCH_02" in evaluated_ids
        assert "TEST_SLA_12B_BATCH_03" not in evaluated_ids

        # Verify DB persistence
        db.refresh(exc1)
        db.refresh(exc2)
        db.refresh(exc3)

        assert exc1.sla_status == "OK"
        assert exc2.sla_status == "WARNING"
        assert exc3.sla_status == "OK"  # Historical approved preserved
    finally:
        db.close()
