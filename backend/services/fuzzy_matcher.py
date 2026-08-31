"""
ReconcileAI - Fuzzy Matching Engine (Phase 7)

Second-pass matching layer that runs AFTER Phase 6 deterministic rules.
Uses RapidFuzz string similarity to match near-identical:
  - Reference IDs / payment IDs  (e.g. pay_1045 vs PAY-1045)
  - UTRs / bank narrations
  - Transaction descriptions
  - Customer / payer names

Decision ladder per candidate pair:
  composite_score >= FUZZY_MATCH_THRESHOLD   -> FUZZY_MATCHED  (auto-resolved)
  composite_score >= FUZZY_REVIEW_THRESHOLD  -> FUZZY_REVIEW   (human review)
  composite_score <  FUZZY_REVIEW_THRESHOLD  -> FUZZY_NO_MATCH (escalate)

No LLM calls. No probabilistic AI. 100% deterministic for a given threshold.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Any

from rapidfuzz import fuzz

from backend.config import settings


# ---------------------------------------------------------------------------
# Reason-code constants
# ---------------------------------------------------------------------------

class FuzzyReasonCode:
    FUZZY_MATCHED    = "FUZZY_MATCHED | COMPOSITE_SCORE_ABOVE_THRESHOLD"
    FUZZY_REVIEW     = "FUZZY_REVIEW | COMPOSITE_SCORE_IN_REVIEW_BAND"
    FUZZY_NO_MATCH   = "FUZZY_NO_MATCH | COMPOSITE_SCORE_BELOW_THRESHOLD"
    REF_FUZZY        = "REFERENCE_FUZZY_MATCH"
    DESC_FUZZY       = "DESCRIPTION_FUZZY_MATCH"
    CUSTOMER_FUZZY   = "CUSTOMER_NAME_FUZZY_MATCH"
    AMOUNT_CONFIRMED = "AMOUNT_CONFIRMED_EXACT"
    AMOUNT_WARNING   = "AMOUNT_DIFFERS_AFTER_FUZZY_MATCH"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class FuzzyMatchResult:
    """
    Outcome of a single fuzzy candidate-pair evaluation.

    Fields
    ------
    match_id          : Unique ID for this fuzzy match attempt.
    gateway_txn_id    : transaction_id of the Gateway record.
    bank_txn_id       : transaction_id of the Bank record (None for orphans).
    reference_score   : RapidFuzz score for reference_id similarity  (0-100).
    description_score : RapidFuzz score for description similarity    (0-100).
    customer_score    : RapidFuzz score for customer_name similarity  (0-100).
    composite_score   : Weighted average of the above scores          (0-100).
    decision          : FUZZY_MATCHED | FUZZY_REVIEW | FUZZY_NO_MATCH.
    reason_codes      : List of FuzzyReasonCode strings that fired.
    matched_fields    : Which field(s) drove the match.
    amount_match      : True when gateway and bank amounts agree exactly.
    amount_diff       : Absolute difference between amounts (INR).
    """
    match_id: str
    gateway_txn_id: Optional[str]
    bank_txn_id: Optional[str]
    reference_score: float
    description_score: float
    customer_score: float
    composite_score: float
    decision: str
    reason_codes: List[str] = field(default_factory=list)
    matched_fields: List[str] = field(default_factory=list)
    amount_match: bool = False
    amount_diff: float = 0.0


# ---------------------------------------------------------------------------
# Text normalisation helpers
# ---------------------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^a-z0-9]")


def _normalise(text: Optional[str]) -> str:
    """Strip punctuation, lowercase. Used for reference IDs and descriptions."""
    if not text:
        return ""
    return _NON_ALNUM.sub("", text.lower())


def _normalise_name(text: Optional[str]) -> str:
    """Preserve spaces for customer-name WRatio comparison."""
    if not text:
        return ""
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class FuzzyMatchEngine:
    """
    Phase 7 fuzzy matching engine.

    Typical usage
    -------------
    engine = FuzzyMatchEngine()

    # Score a single GW-Bank pair
    result = engine.score_pair(gateway_txn, bank_txn)

    # Batch: find best fuzzy candidate for each unmatched gateway record
    pairs = engine.find_best_candidates(unmatched_gateways, unmatched_banks)
    """

    def __init__(
        self,
        match_threshold: float = settings.FUZZY_MATCH_THRESHOLD,
        review_threshold: float = settings.FUZZY_REVIEW_THRESHOLD,
        ref_weight: float = 0.50,
        desc_weight: float = 0.30,
        customer_weight: float = 0.20,
    ) -> None:
        self.match_threshold  = match_threshold
        self.review_threshold = review_threshold
        self.ref_weight       = ref_weight
        self.desc_weight      = desc_weight
        self.customer_weight  = customer_weight

    # ------------------------------------------------------------------
    # Field scorers
    # ------------------------------------------------------------------

    def score_reference_similarity(
        self, ref_a: Optional[str], ref_b: Optional[str]
    ) -> float:
        """
        Compare two payment reference IDs or UTRs.

        Uses token_sort_ratio (handles token reordering) and partial_ratio
        (handles prefix/suffix padding common in bank refs).
        Returns the higher of the two scores.
        """
        a, b = _normalise(ref_a), _normalise(ref_b)
        if not a or not b:
            return 0.0
        return float(max(fuzz.token_sort_ratio(a, b), fuzz.partial_ratio(a, b)))

    def score_description_similarity(
        self, desc_a: Optional[str], desc_b: Optional[str]
    ) -> float:
        """
        Compare a bank narration against a gateway description.

        partial_ratio is used because bank narrations often embed the payment
        reference inside a longer sentence (e.g. 'NEFT CR pay1045 RAZORPAY').
        """
        a, b = _normalise(desc_a), _normalise(desc_b)
        if not a or not b:
            return 0.0
        return float(fuzz.partial_ratio(a, b))

    def score_customer_similarity(
        self, name_a: Optional[str], name_b: Optional[str]
    ) -> float:
        """
        Compare customer / payer names.

        WRatio combines multiple RapidFuzz scorers internally and handles
        abbreviations, middle-name omissions, and initial differences well.
        """
        a, b = _normalise_name(name_a), _normalise_name(name_b)
        if not a or not b:
            return 0.0
        return float(fuzz.WRatio(a, b))

    # ------------------------------------------------------------------
    # Composite pair scorer
    # ------------------------------------------------------------------

    def score_pair(self, gw: Any, bank: Any) -> FuzzyMatchResult:
        """
        Evaluate fuzzy similarity between one Gateway and one Bank record.

        Parameters
        ----------
        gw, bank : CanonicalTransaction or Transaction objects with attributes:
                   reference_id, description, customer_name, amount, transaction_id
        """
        match_id   = str(uuid.uuid4())
        ref_score  = self.score_reference_similarity(
            getattr(gw,   "reference_id", None),
            getattr(bank, "reference_id", None),
        )
        desc_score = self.score_description_similarity(
            getattr(gw,   "description", None),
            getattr(bank, "description", None),
        )
        cust_score = self.score_customer_similarity(
            getattr(gw,   "customer_name", None),
            getattr(bank, "customer_name", None),
        )
        composite = (
            ref_score  * self.ref_weight +
            desc_score * self.desc_weight +
            cust_score * self.customer_weight
        )

        gw_amt     = float(getattr(gw,   "amount", 0) or 0)
        bank_amt   = float(getattr(bank, "amount", 0) or 0)
        amount_diff  = abs(gw_amt - bank_amt)
        amount_match = amount_diff == 0.0

        matched_fields: List[str] = []
        reason_codes:  List[str] = []

        if ref_score  >= self.match_threshold:
            matched_fields.append("reference_id")
            reason_codes.append(FuzzyReasonCode.REF_FUZZY)
        if desc_score >= self.match_threshold:
            matched_fields.append("description")
            reason_codes.append(FuzzyReasonCode.DESC_FUZZY)
        if cust_score >= self.match_threshold:
            matched_fields.append("customer_name")
            reason_codes.append(FuzzyReasonCode.CUSTOMER_FUZZY)

        reason_codes.append(
            FuzzyReasonCode.AMOUNT_CONFIRMED if amount_match
            else FuzzyReasonCode.AMOUNT_WARNING
        )

        if composite >= self.match_threshold:
            decision = "FUZZY_MATCHED"
            reason_codes.insert(0, FuzzyReasonCode.FUZZY_MATCHED)
        elif composite >= self.review_threshold:
            decision = "FUZZY_REVIEW"
            reason_codes.insert(0, FuzzyReasonCode.FUZZY_REVIEW)
        else:
            decision = "FUZZY_NO_MATCH"
            reason_codes.insert(0, FuzzyReasonCode.FUZZY_NO_MATCH)

        return FuzzyMatchResult(
            match_id          = match_id,
            gateway_txn_id    = getattr(gw,   "transaction_id", None),
            bank_txn_id       = getattr(bank, "transaction_id", None),
            reference_score   = round(ref_score,   2),
            description_score = round(desc_score,  2),
            customer_score    = round(cust_score,  2),
            composite_score   = round(composite,   2),
            decision          = decision,
            reason_codes      = reason_codes,
            matched_fields    = matched_fields,
            amount_match      = amount_match,
            amount_diff       = round(amount_diff, 2),
        )

    # ------------------------------------------------------------------
    # Batch candidate finder
    # ------------------------------------------------------------------

    def find_best_candidates(
        self,
        unmatched_gateways: List[Any],
        unmatched_banks:    List[Any],
    ) -> List[FuzzyMatchResult]:
        """
        Greedy one-to-one fuzzy matching across two lists.

        For each gateway record:
          1. Score it against every available bank record.
          2. Keep the highest-scoring pair.
          3. Remove the winning bank from the pool (prevents double-matching).
          4. If no banks remain, emit a FUZZY_NO_MATCH orphan result.

        Complexity: O(n x m) — appropriate for typical batch sizes.
        """
        results: List[FuzzyMatchResult] = []
        available_banks = list(unmatched_banks)

        for gw in unmatched_gateways:
            if not available_banks:
                results.append(FuzzyMatchResult(
                    match_id          = str(uuid.uuid4()),
                    gateway_txn_id    = getattr(gw, "transaction_id", None),
                    bank_txn_id       = None,
                    reference_score   = 0.0,
                    description_score = 0.0,
                    customer_score    = 0.0,
                    composite_score   = 0.0,
                    decision          = "FUZZY_NO_MATCH",
                    reason_codes      = [FuzzyReasonCode.FUZZY_NO_MATCH],
                    matched_fields    = [],
                    amount_match      = False,
                    amount_diff       = float(getattr(gw, "amount", 0) or 0),
                ))
                continue

            best: Optional[FuzzyMatchResult] = None
            for bank in available_banks:
                cand = self.score_pair(gw, bank)
                if best is None or cand.composite_score > best.composite_score:
                    best = cand

            if best and best.bank_txn_id is not None:
                available_banks = [
                    b for b in available_banks
                    if getattr(b, "transaction_id", None) != best.bank_txn_id
                ]

            results.append(best)  # type: ignore[arg-type]

        return results