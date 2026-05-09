# Copyright (c) 2026 Divya Rajaraman
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

"""V2 synthetic data reconciliation — detailed human-readable report.

Run this after any regeneration of the synthetic data to verify that
internal event-level dollar amounts still sum to CMS Open Payments totals
per HCP × year (the core invariant of the v2ator design).

For CI-friendly pass/fail gates, see tests/test_v2_reconciliation.py.

CMS source note:
    We use hcp_master.parquet's cms_total_* columns as the CMS reference.
    These are aggregated at generation time from raw stg_cms_general_payments
    filtered by is_target=TRUE (Takeda only). Verified 100% match to raw CMS
    (0.00 max diff across 291,033 HCP-year tuples) — so hcp_master is a valid
    proxy and much faster than round-tripping through Athena.

    For a per-HCP drilldown that queries raw CMS directly, see:
        scripts/validate/reconcile_hcp_drilldown.py

Usage:
    python scripts/validate/reconcile_v2_report.py

Prerequisites:
    - data/processed/hcp_interactions.parquet
    - data/processed/hcp_master.parquet
    - S3 access to s3://compliance-risk-investigator/synthetic/speaker_programs/
"""
from __future__ import annotations

from pathlib import Path

import awswrangler as wr
import pandas as pd


REPO_ROOT         = Path(__file__).resolve().parents[2]
INTERACTIONS_PATH = REPO_ROOT / "data" / "processed" / "hcp_interactions.parquet"
MASTER_PATH       = REPO_ROOT / "data" / "processed" / "hcp_master.parquet"
SPEAKERS_S3       = "s3://compliance-risk-investigator/synthetic/speaker_programs/speaker_program_events.parquet"


def build_reconciliation_df() -> pd.DataFrame:
    """Merged (hcp_id, year, cms_total, synth_int, synth_spk, synth, gap) frame."""
    interactions = pd.read_parquet(INTERACTIONS_PATH)
    master       = pd.read_parquet(MASTER_PATH)
    speakers     = wr.s3.read_parquet(SPEAKERS_S3)

    interactions["year"] = pd.to_datetime(interactions["interaction_date"]).dt.year

    int_amt = interactions.assign(
        total_amt=lambda d: d["payment_amount"].fillna(0) + d["travel_reimbursement"].fillna(0)
    )
    int_synth = (
        int_amt.groupby(["hcp_id", "year"])["total_amt"]
        .sum()
        .reset_index()
        .rename(columns={"total_amt": "synth_int"})
    )

    spk = speakers.assign(
        total=lambda d: d["speaker_fee"].fillna(0) + d["travel_reimbursement"].fillna(0)
    )
    spk_synth = (
        spk.groupby(["speaker_hcp_id", "program_year"])["total"]
        .sum()
        .reset_index()
        .rename(columns={"speaker_hcp_id": "hcp_id", "program_year": "year", "total": "synth_spk"})
    )

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


def main() -> None:
    print("=" * 72)
    print("V2 Synthetic Data Reconciliation Report")
    print("=" * 72)
    print("Source:")
    print(f"  Interactions: {INTERACTIONS_PATH}")
    print(f"  Master:       {MASTER_PATH}")
    print(f"  Speakers:     {SPEAKERS_S3}")
    print()

    m = build_reconciliation_df()
    nz = m[m["cms_total"] > 0].copy()
    nz["rel_gap"] = nz["abs_gap"] / nz["cms_total"]

    perfect  = (nz["rel_gap"] < 0.01).sum()
    minor    = ((nz["rel_gap"] >= 0.01) & (nz["rel_gap"] < 0.05)).sum()
    moderate = ((nz["rel_gap"] >= 0.05) & (nz["rel_gap"] < 0.10)).sum()
    major    = (nz["rel_gap"] >= 0.10).sum()
    total    = len(nz)

    print("Coverage")
    print(f"  Total HCP x year tuples:           {len(m):,}")
    print(f"  Tuples with CMS > 0 (reconciled):  {total:,}")
    print(f"  Tuples with CMS = 0 (no-payment):  {len(m) - total:,}")
    print()

    print("Reconciliation Distribution (tuples with CMS > 0)")
    print(f"  Perfect   (<1%   gap):  {perfect:>8,}  ({perfect/total*100:>5.1f}%)")
    print(f"  Minor     (1-5%  gap):  {minor:>8,}  ({minor/total*100:>5.1f}%)")
    print(f"  Moderate  (5-10% gap):  {moderate:>8,}  ({moderate/total*100:>5.1f}%)")
    print(f"  Major     (>=10% gap):  {major:>8,}  ({major/total*100:>5.1f}%)")
    print("  ** Major gaps include intentional 5% serious-profile anomalies")
    print("     seeded at 80-88% match to represent real under-reporting violations.")
    print()

    print("Dollar Totals")
    print(f"  Total synthetic $: ${m['synth'].sum():>15,.0f}")
    print(f"  Total CMS $:       ${m['cms_total'].sum():>15,.0f}")
    print(f"  Overall gap:       ${m['gap'].sum():>15,.0f}")
    print(f"  Overall gap %:     {abs(m['gap'].sum()) / m['cms_total'].sum() * 100:>15.2f}%")
    print()

    print("Relative Gap Statistics")
    print(f"  Mean abs rel gap:   {nz['rel_gap'].mean()*100:>6.2f}%")
    print(f"  Median abs rel gap: {nz['rel_gap'].median()*100:>6.2f}%")
    print(f"  P95 abs rel gap:    {nz['rel_gap'].quantile(0.95)*100:>6.2f}%")
    print(f"  Max abs rel gap:    {nz['rel_gap'].max()*100:>6.2f}%")
    print()

    print("Per-Year Breakdown")
    for year in sorted(nz["year"].unique()):
        year_df = nz[nz["year"] == year]
        perfect_pct = (year_df["rel_gap"] < 0.01).mean() * 100
        synth_sum = year_df["synth"].sum()
        cms_sum = year_df["cms_total"].sum()
        print(f"  {year}:  {len(year_df):>6,} HCPs  |  "
              f"perfect: {perfect_pct:>5.1f}%  |  "
              f"synth ${synth_sum/1e6:>6.1f}M  vs  cms ${cms_sum/1e6:>6.1f}M")
    print()

    print("Sample: 3 largest reconciled HCPs in 2023")
    top = nz[nz["year"] == 2023].nlargest(3, "cms_total")
    for _, r in top.iterrows():
        print(f"  {r['hcp_id']}: cms=${r['cms_total']:>10,.2f}  "
              f"synth=${r['synth']:>10,.2f}  "
              f"gap=${r['gap']:>7,.2f} ({r['rel_gap']*100:.2f}%)")


if __name__ == "__main__":
    main()
