"""V2 synthetic data reconciliation tests.

Verifies that synthetic dollar amounts reconcile to CMS Open Payments totals
per HCP × year. The v2 generator seeds interactions and speaker events from
real Takeda CMS data, so sums should match within tolerance for the vast
majority of HCPs.

Tolerance bands:
  - Perfect: relative gap < 1%        (design target)
  - Minor:   1-5%   (acceptable Dirichlet rounding noise)
  - Moderate: 5-10% (borderline - investigate)
  - Major:   >= 10% (includes intentional 5% serious-profile anomalies
                    seeded at 80-88% match to represent real under-reporting)

Usage:
    pytest tests/test_v2_reconciliation.py -v

Requires:
    - data/processed/hcp_interactions.parquet
    - data/processed/hcp_master.parquet
    - S3 access to speaker_program_events.parquet
"""
from __future__ import annotations

from pathlib import Path

import awswrangler as wr
import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INTERACTIONS_PATH = REPO_ROOT / "data" / "processed" / "hcp_interactions.parquet"
MASTER_PATH       = REPO_ROOT / "data" / "processed" / "hcp_master.parquet"
SPEAKERS_S3       = "s3://compliance-risk-investigator/synthetic/speaker_programs/speaker_program_events.parquet"


@pytest.fixture(scope="module")
def reconciliation_df() -> pd.DataFrame:
    """Build the merged (CMS_total, synth_total) DataFrame used by all tests."""
    if not INTERACTIONS_PATH.exists() or not MASTER_PATH.exists():
        pytest.skip("Synthetic data parquets not found locally")

    interactions = pd.read_parquet(INTERACTIONS_PATH)
    master       = pd.read_parquet(MASTER_PATH)
    try:
        speakers = wr.s3.read_parquet(SPEAKERS_S3)
    except Exception as e:
        pytest.skip(f"S3 access to speaker_program_events failed: {e}")

    interactions["year"] = pd.to_datetime(interactions["interaction_date"]).dt.year

    # Sum all interaction-level dollars.
    # payment_amount covers the Dirichlet-split CMS allocation across events
    # (includes meals + consulting + education). travel_reimbursement is
    # allocated separately and adds on top.
    int_amt = interactions.assign(
        total_amt=lambda d: d["payment_amount"].fillna(0) + d["travel_reimbursement"].fillna(0)
    )
    int_synth = (
        int_amt.groupby(["hcp_id", "year"])["total_amt"]
        .sum()
        .reset_index()
        .rename(columns={"total_amt": "synth_int"})
    )

    # Speaker events: speaker_fee is the per-HCP payment; travel_reimbursement
    # is their share of travel. total_program_cost bundles venue costs that
    # aren't per-HCP payments, so don't use that column here.
    spk = speakers.assign(
        total=lambda d: d["speaker_fee"].fillna(0) + d["travel_reimbursement"].fillna(0)
    )
    spk_synth = (
        spk.groupby(["speaker_hcp_id", "program_year"])["total"]
        .sum()
        .reset_index()
        .rename(columns={"speaker_hcp_id": "hcp_id", "program_year": "year", "total": "synth_spk"})
    )

    # CMS totals per HCP × year from hcp_master.
    cms_rows = []
    for year in (2022, 2023, 2024):
        col = f"cms_total_{year}"
        cms_rows.append(master[["hcp_id", col]].rename(columns={col: "cms_total"}).assign(year=year))
    cms_df = pd.concat(cms_rows, ignore_index=True)

    m = cms_df.merge(int_synth, on=["hcp_id", "year"], how="left").fillna(0)
    m = m.merge(spk_synth, on=["hcp_id", "year"], how="left").fillna(0)
    m["synth"]   = m["synth_int"] + m["synth_spk"]
    m["gap"]     = m["synth"] - m["cms_total"]
    m["abs_gap"] = m["gap"].abs()

    return m


def test_perfect_reconciliation_rate(reconciliation_df: pd.DataFrame) -> None:
    """At least 95% of HCP-year tuples with CMS > 0 should reconcile within 1%."""
    nz = reconciliation_df[reconciliation_df["cms_total"] > 0].copy()
    nz["rel_gap"] = nz["abs_gap"] / nz["cms_total"]

    perfect_rate = (nz["rel_gap"] < 0.01).mean()
    assert perfect_rate >= 0.95, (
        f"Only {perfect_rate*100:.1f}% of HCP-year tuples recocile within 1% "
        f"(expected >= 95%)"
    )


def test_overall_gap_under_1_percent(reconciliation_df: pd.DataFrame) -> None:
    """Total synthetic dollars should be within 1% of total CMS dollars."""
    total_synth = reconciliation_df["synth"].sum()
    total_cms   = reconciliation_df["cms_total"].sum()
    gap_pct     = abs(total_synth - total_cms) / total_cms

    assert gap_pct < 0.01, (
        f"Overall gap {gap_pct*100:.2f}% exceeds 1% threshold "
        f"(synth=${total_synth:,.0f}, cms=${total_cms:,.0f})"
    )


def test_median_relative_gap_near_zero(reconciliation_df: pd.DataFrame) -> None:
    """Median relative gap should be effectively zero (< 0.5%)."""
    nz = reconciliation_df[reconciliation_df["cms_total"] > 0].copy()
    nz["rel_gap"] = nz["abs_gap"] / nz["cms_total"]

    median = nz["rel_gap"].median()
    assert median < 0.005, f"Median relative gap {median*100:.2f}% exceeds 0.5%"


def test_major_gap_rate_under_5_percent(reconciliation_df: pd.DataFrame) -> None:
    """Major gaps (>= 10%) should be < 5% (includes intentional anomaly seeds)."""
    nz = reconciliation_df[reconciliation_df["cms_total"] > 0].copy()
    nz["rel_gap"] = nz["abs_gap"] / nz["cms_total"]

    major_rate = (nz["rel_gap"] >= 0.10).mean()
    assert major_rate < 0.05, (
        f"{major_rate*100:.1f}% of HCP-year tuples have >= 10% gap "
        f"(expected < 5%, includes intentional 5% serious-profile anomalies)"
    )


def test_synth_spend_interaction_correlation(reconciliation_df: pd.DataFrame) -> None:
    """Spend-interaction correlation should be in the realistic 0.4-0.7 range."""
    interactions = pd.read_parquet(INTERACTIONS_PATH)
    interactions["year"] = pd.to_datetime(interactions["interaction_date"]).dt.year
    int_count = (
        interactions.groupby(["hcp_id", "year"])
        .size()
        .reset_index(name="n_int")
    )

    m = reconciliation_df.merge(int_count, on=["hcp_id", "year"], how="left").fillna(0)
    nz = m[m["cms_total"] > 0]

    corr = nz["cms_total"].corr(nz["n_int"])
    assert 0.3 < corr < 0.8, (
        f"Spend-interaction correlation {corr:.3f} outside expected 0.3-0.8 range "
        f"(real CMS data typically shows 0.4-0.7)"
    )


if __name__ == "__main__":
    # Allow `python tests/test_v2_reconciliation.py` for a quick ad-hoc report.
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
