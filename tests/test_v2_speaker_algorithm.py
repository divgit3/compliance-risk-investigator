# Copyright (c) 2026 Divya Rajaraman
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

"""
tests/test_v2_speaker_algorithm.py — Option W v2 speaker fee algorithm validation

Validates the v2 CMS-reconciled speaker fee generation algorithm in isolation,
before porting into synthetic_generator.py.

Design premise (reconciliation invariant):
  CMS total is ground truth. synthetic events must SUM to that total.
  Internal event records show WHERE the CMS dollars went — they don't invent
  new money. Priority-speaker bypass in v1 broke this invariant for 98.9% of
  events; this algorithm fixes that by deriving n_events from the CMS total.

Algorithm under test:
  compute_event_fees(cms_total, profile, rng)
    - cms_total < MIN_PLAUSIBLE_SPEAKER_CMS → skip (return empty array)
    - n_events derived from cms_total / REALISTIC_PER_EVENT_TARGET, capped at MAX
    - Dirichlet split parameterised by profile alpha → natural variance
    - sum(fees) == cms_total by construction (within float rounding)

Run:
  pytest tests/test_v2_speaker_algorithm.py -v
  pytest tests/test_v2_speaker_algorithm.py -v -m "not slow"
"""

from __future__ import annotations

import numpy as np
import pytest

from pipelines.business_rules_registry import get_rule

# ── Module-level constants matching the v2 design ─────────────────────────────

MIN_PLAUSIBLE_SPEAKER_CMS   = 1_000       # skip HCPs whose real CMS total is too low
REALISTIC_PER_EVENT_TARGET  = 2_000       # target dollars per speaker event
MAX_PLAUSIBLE_EVENTS        = 24          # hard cap on events per HCP per year
RANDOM_SEED                 = 42

FMV_CEILING              = get_rule("SPEAKER_001")["effective_threshold"]  # 4000.0
REPEAT_SPEAKER_THRESHOLD = get_rule("SPEAKER_002")["effective_threshold"]  # 6


# ── Algorithm under test ───────────────────────────────────────────────────────

def compute_event_fees(
    cms_total: float,
    profile: str,
    rng: np.random.Generator,
) -> np.ndarray:
    """
    Generate per-event speaker fees that sum to cms_total.

    Parameters
    ----------
    cms_total : float
        Real CMS-reported total spend for this HCP-year.
    profile : str
        Compliance profile key — controls Dirichlet concentration (alpha).
        One of: "clean", "minor", "moderate", "serious".
    rng : np.random.Generator
        Seeded RNG for reproducibility.

    Returns
    -------
    np.ndarray
        Array of per-event fees (empty if cms_total < MIN_PLAUSIBLE_SPEAKER_CMS).
        sum(result) ≈ cms_total (within $0.05 rounding tolerance).
    """
    if cms_total < MIN_PLAUSIBLE_SPEAKER_CMS:
        return np.array([])

    n_events = max(1, round(cms_total / REALISTIC_PER_EVENT_TARGET))
    n_events = min(n_events, MAX_PLAUSIBLE_EVENTS)

    alpha_map = {
        "clean":    2.0,
        "minor":    1.5,
        "moderate": 1.0,
        "serious":  0.6,
    }
    alpha = alpha_map[profile]

    splits = rng.dirichlet([alpha] * n_events)
    fees   = (splits * cms_total).round(2)
    return fees


# ── Parametrised test data (real CMS-verified data points) ────────────────────
#
# Fields: (hcp_id, year, cms_total, profile, expect_skip)
#   expect_skip=True  → cms_total < MIN_PLAUSIBLE_SPEAKER_CMS, no events generated
#   expect_skip=False → algorithm runs, fees must reconcile

_CASES = [
    ("888108",  2022,    89.36, "moderate", True),   # $89  < MIN → skip
    ("943738",  2022,  1500.00, "moderate", False),
    ("314424",  2024,  5015.00, "clean",    False),
    ("881249",  2023,  1270.63, "serious",  False),  # $1270 > MIN → one event
    ("881249",  2024,  3752.00, "serious",  False),
    ("194746",  2022, 15080.00, "moderate", False),
    ("78130",   2022, 10822.00, "clean",    False),
    ("13577",   2022, 88743.75, "serious",  False),
    ("13577",   2023, 83197.50, "serious",  False),
]

_CASE_IDS = [f"{hcp}-{yr}" for hcp, yr, *_ in _CASES]


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(RANDOM_SEED)


# ── Tests ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("hcp_id,year,cms_total,profile,expect_skip", _CASES, ids=_CASE_IDS)
def test_below_threshold_skips(hcp_id, year, cms_total, profile, expect_skip, rng):
    """Cases flagged expect_skip must return an empty array."""
    fees = compute_event_fees(cms_total, profile, rng)
    if expect_skip:
        assert fees.size == 0, (
            f"HCP {hcp_id}/{year}: expected skip (cms={cms_total}) "
            f"but got {fees.size} events"
        )
    else:
        # Non-skip cases must produce at least one event — verified by other tests;
        # here we simply confirm they are not empty so parametrize covers all rows.
        assert fees.size > 0, (
            f"HCP {hcp_id}/{year}: unexpected empty fees for cms={cms_total}"
        )


@pytest.mark.parametrize("hcp_id,year,cms_total,profile,expect_skip", _CASES, ids=_CASE_IDS)
def test_reconciliation_invariant(hcp_id, year, cms_total, profile, expect_skip, rng):
    """Non-skip cases: sum(fees) must equal cms_total within $0.05 rounding tolerance."""
    if expect_skip:
        pytest.skip("skip case — reconciliation invariant not applicable")

    fees = compute_event_fees(cms_total, profile, rng)
    delta = abs(fees.sum() - cms_total)
    assert delta < 0.05, (
        f"HCP {hcp_id}/{year}: sum({fees.sum():.2f}) ≠ cms_total({cms_total:.2f}), "
        f"delta={delta:.4f}"
    )


@pytest.mark.parametrize("hcp_id,year,cms_total,profile,expect_skip", _CASES, ids=_CASE_IDS)
def test_event_count_reasonable(hcp_id, year, cms_total, profile, expect_skip, rng):
    """n_events must be ≥1, ≤ MAX_PLAUSIBLE_EVENTS, and within ±1 of the target ratio."""
    if expect_skip:
        pytest.skip("skip case — event count not applicable")

    fees = compute_event_fees(cms_total, profile, rng)
    n = fees.size

    assert n >= 1, f"HCP {hcp_id}/{year}: expected ≥1 event, got {n}"
    assert n <= MAX_PLAUSIBLE_EVENTS, (
        f"HCP {hcp_id}/{year}: {n} events exceeds MAX_PLAUSIBLE_EVENTS={MAX_PLAUSIBLE_EVENTS}"
    )

    expected_n = round(cms_total / REALISTIC_PER_EVENT_TARGET)
    capped_n   = min(max(1, expected_n), MAX_PLAUSIBLE_EVENTS)
    assert abs(n - capped_n) <= 1, (
        f"HCP {hcp_id}/{year}: n_events={n} deviates by more than ±1 "
        f"from expected={capped_n} (cms={cms_total})"
    )


def test_high_cms_triggers_repeat_speaker():
    """
    HCP 13577 with $88 743 CMS → n_events should organically exceed
    REPEAT_SPEAKER_THRESHOLD (6), confirming the v2 algorithm surfaces
    repeat-speaker compliance violations without a priority-speaker hack.
    """
    rng      = np.random.default_rng(RANDOM_SEED)
    cms      = 88_743.75
    fees     = compute_event_fees(cms, "serious", rng)
    n_events = fees.size

    assert n_events > REPEAT_SPEAKER_THRESHOLD, (
        f"Expected n_events > {REPEAT_SPEAKER_THRESHOLD} for cms={cms}, "
        f"but got {n_events}. Repeat-speaker violation won't fire."
    )


def test_serious_profile_higher_variance():
    """
    'serious' profile (alpha=0.6) must produce higher per-event fee variance
    than 'clean' profile (alpha=2.0) for the same CMS total.

    Variance is measured as the mean coefficient of variation (CoV = std/mean)
    across 100 independently-seeded runs.  Higher alpha → more uniform split
    (lower CoV); lower alpha → more concentrated split (higher CoV).
    """
    cms_total = 10_000.0
    n_runs    = 100

    cov_serious = []
    cov_clean   = []

    for seed in range(n_runs):
        rng_s = np.random.default_rng(seed)
        rng_c = np.random.default_rng(seed)

        fees_s = compute_event_fees(cms_total, "serious", rng_s)
        fees_c = compute_event_fees(cms_total, "clean",   rng_c)

        # Both should produce fees; n_events is the same (derived from cms_total)
        if fees_s.size > 1:
            cov_serious.append(fees_s.std() / fees_s.mean())
        if fees_c.size > 1:
            cov_clean.append(fees_c.std() / fees_c.mean())

    mean_cov_serious = float(np.mean(cov_serious))
    mean_cov_clean   = float(np.mean(cov_clean))

    assert mean_cov_serious > mean_cov_clean, (
        f"Expected serious CoV ({mean_cov_serious:.4f}) > "
        f"clean CoV ({mean_cov_clean:.4f}). "
        "Lower alpha should produce more concentrated (higher-variance) splits."
    )


def test_fmv_violations_emerge_at_constrained_high_cms():
    """
    An HCP with very high CMS total ($150 000) capped at MAX_PLAUSIBLE_EVENTS=24
    must have at least one per-event fee exceeding FMV_CEILING.

    Expected_n = round(150000/2000) = 75 → capped to 24.
    Average fee = 150000/24 ≈ $6 250, which is above FMV_CEILING.
    This confirms organic FMV violations emerge from the reconciliation math
    when CMS dollars are high but events are capped — no special flag needed.
    """
    rng       = np.random.default_rng(RANDOM_SEED)
    cms_total = 150_000.0
    fees      = compute_event_fees(cms_total, "clean", rng)

    assert fees.size > 0, "Expected non-empty fees for cms=150 000"
    assert fees.max() > FMV_CEILING, (
        f"Expected at least one fee > FMV_CEILING ({FMV_CEILING}), "
        f"but max fee was {fees.max():.2f}. "
        "FMV violations should emerge organically when CMS is high and events are capped."
    )


def test_deterministic_with_seed():
    """Same seed + same inputs must produce identical fees arrays."""
    cms     = 5_015.00
    profile = "clean"

    fees_a = compute_event_fees(cms, profile, np.random.default_rng(RANDOM_SEED))
    fees_b = compute_event_fees(cms, profile, np.random.default_rng(RANDOM_SEED))

    np.testing.assert_array_equal(
        fees_a, fees_b,
        err_msg="compute_event_fees is not deterministic for the same seed",
    )
