"""V2 reconciliation drilldown for a single HCP.

Prints CMS payment records (source of truth) alongside the synthetic events
(interactions + speaker programs) generated for the same HCP, with year-level
and overall totals.

Usage:
    python scripts/validate/reconcile_hcp_drilldown.py HCP_357811
    python scripts/validate/reconcile_hcp_drilldown.py HCP_37769 --year 2023
"""
from __future__ import annotations

import argparse
from pathlib import Path

import awswrangler as wr
import pandas as pd


REPO_ROOT         = Path(__file__).resolve().parents[2]
INTERACTIONS_PATH = REPO_ROOT / "data" / "processed" / "hcp_interactions.parquet"
MASTER_PATH       = REPO_ROOT / "data" / "processed" / "hcp_master.parquet"
SPEAKERS_S3       = "s3://compliance-risk-investigator/synthetic/speaker_programs/speaker_program_events.parquet"


def load_cms_records(hcp_id: str) -> pd.DataFrame:
    """Fetch the raw CMS Open Payments rows for a single HCP via Athena."""
    sql = f"""
    SELECT
      program_year,
      date_of_payment,
      nature_of_payment   AS category,
      payment_amount      AS amount,
      company_name        AS payer,
      product_name_1      AS product,
      is_target,
      is_competitor
    FROM compliance_risk_raw.stg_cms_general_payments
    WHERE hcp_id = '{hcp_id}'
    ORDER BY program_year, date_of_payment
    """
    try:
        return wr.athena.read_sql_query(
            sql=sql,
            database="compliance_risk_raw",
            s3_output="s3://compliance-risk-investigator/athena-results/",
        )
    except Exception as e:
        print(f"  [warn] Athena query failed: {e}")
        return pd.DataFrame()


def main(hcp_id: str, year_filter: int | None) -> None:
    print("=" * 76)
    print(f"Reconciliation Drilldown: {hcp_id}" + (f"  (year={year_filter})" if year_filter else ""))
    print("=" * 76)

    # Master row for context
    master = pd.read_parquet(MASTER_PATH)
    row = master[master["hcp_id"] == hcp_id]
    if row.empty:
        print(f"[error] {hcp_id} not found in hcp_master.")
        return
    r = row.iloc[0]
    print(f"\nHCP Profile")
    print(f"  specialty:        {r.get('specialty', 'N/A')}")
    print(f"  state:            {r.get('state', 'N/A')}")
    print(f"  fmv_tier:         {r.get('fmv_tier', 'N/A')}")
    print(f"  is_kol:           {r.get('is_kol', 'N/A')}")
    print(f"  profile:          {r.get('hcp_violation_profile', 'N/A')}")
    print(f"  cms_total_2022:   ${r.get('cms_total_2022', 0):>12,.2f}")
    print(f"  cms_total_2023:   ${r.get('cms_total_2023', 0):>12,.2f}")
    print(f"  cms_total_2024:   ${r.get('cms_total_2024', 0):>12,.2f}")

    # CMS records (source of truth)
    print(f"\n{'-'*76}")
    print("CMS Open Payments records (source of truth)")
    print(f"{'-'*76}")
    cms = load_cms_records(hcp_id)
    if year_filter and not cms.empty:
        cms = cms[cms["program_year"] == year_filter]
    if cms.empty:
        print("  [none]")
    else:
        cms_target     = cms[cms["is_target"] == True]
        cms_competitor = cms[cms["is_competitor"] == True]
        cms_pop_only   = cms[(cms["is_target"] == False) & (cms["is_competitor"] == False)]

        print(f"  Total records: {len(cms)} | Total amount: ${cms['amount'].sum():,.2f}")
        print(f"    Takeda (target):    {len(cms_target):>4} records | ${cms_target['amount'].sum():>14,.2f}  <-- synthetic generator target")
        print(f"    Competitor pharma:  {len(cms_competitor):>4} records | ${cms_competitor['amount'].sum():>14,.2f}")
        print(f"    Other pharma only:  {len(cms_pop_only):>4} records | ${cms_pop_only['amount'].sum():>14,.2f}")

        if not cms_target.empty:
            print(f"\n  Takeda breakdown by category:")
            by_cat = cms_target.groupby(["program_year", "category"])["amount"].agg(["count", "sum"]).round(2)
            print(f"{by_cat.to_string()}")

    # Synthetic interactions
    print(f"\n{'-'*76}")
    print("Synthetic interactions (hcp_interactions.parquet)")
    print(f"{'-'*76}")
    interactions = pd.read_parquet(INTERACTIONS_PATH)
    interactions["year"] = pd.to_datetime(interactions["interaction_date"]).dt.year
    hcp_int = interactions[interactions["hcp_id"] == hcp_id]
    if year_filter:
        hcp_int = hcp_int[hcp_int["year"] == year_filter]
    if hcp_int.empty:
        print("  [none]")
    else:
        hcp_int = hcp_int.assign(
            total_amt=hcp_int["payment_amount"].fillna(0) + hcp_int["travel_reimbursement"].fillna(0)
        )
        print(f"  {len(hcp_int)} interactions, total ${hcp_int['total_amt'].sum():,.2f}")
        by_yt = (
            hcp_int.groupby(["year", "interaction_type"])
            .agg(n=("interaction_id", "count"),
                 payment=("payment_amount", "sum"),
                 meal=("meal_cost", "sum"),
                 travel=("travel_reimbursement", "sum"))
            .round(2)
        )
        print(f"\n{by_yt.to_string()}")

    # Synthetic speaker events
    print(f"\n{'-'*76}")
    print("Synthetic speaker events (speaker_program_events.parquet)")
    print(f"{'-'*76}")
    try:
        speakers = wr.s3.read_parquet(SPEAKERS_S3)
        hcp_spk = speakers[speakers["speaker_hcp_id"] == hcp_id]
        if year_filter:
            hcp_spk = hcp_spk[hcp_spk["program_year"] == year_filter]
        if hcp_spk.empty:
            print("  [none]")
        else:
            hcp_spk = hcp_spk.assign(
                total=hcp_spk["speaker_fee"].fillna(0) + hcp_spk["travel_reimbursement"].fillna(0)
            )
            print(f"  {len(hcp_spk)} speaker events, total ${hcp_spk['total'].sum():,.2f}")
            by_yr = (
                hcp_spk.groupby("program_year")
                .agg(n=("event_id", "count"),
                     speaker_fee=("speaker_fee", "sum"),
                     travel=("travel_reimbursement", "sum"),
                     total=("total", "sum"))
                .round(2)
            )
            print(f"\n{by_yr.to_string()}")
    except Exception as e:
        print(f"  [warn] failed to load speaker events: {e}")

    # Final reconciliation table
    print(f"\n{'='*76}")
    print("Reconciliation summary (year x total)")
    print(f"{'='*76}")
    years = [year_filter] if year_filter else [2022, 2023, 2024]
    print(f"  {'year':<6} {'CMS total':>14} {'Synth interactions':>20} "
          f"{'Synth speakers':>16} {'Synth total':>14} {'Gap':>12} {'Gap %':>8}")
    for y in years:
        cms_y = float(r.get(f"cms_total_{y}", 0))
        int_y = interactions[(interactions["hcp_id"] == hcp_id) & (interactions["year"] == y)]
        int_sum = (int_y["payment_amount"].fillna(0) + int_y["travel_reimbursement"].fillna(0)).sum()
        try:
            spk_y = speakers[(speakers["speaker_hcp_id"] == hcp_id) & (speakers["program_year"] == y)]
            spk_sum = (spk_y["speaker_fee"].fillna(0) + spk_y["travel_reimbursement"].fillna(0)).sum()
        except Exception:
            spk_sum = 0.0
        synth_total = int_sum + spk_sum
        gap = synth_total - cms_y
        gap_pct = (abs(gap) / cms_y * 100) if cms_y > 0 else 0.0
        print(f"  {y:<6} ${cms_y:>13,.2f} ${int_sum:>19,.2f} "
              f"${spk_sum:>15,.2f} ${synth_total:>13,.2f} ${gap:>11,.2f} {gap_pct:>7.2f}%")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Per-HCP reconciliation drilldown")
    parser.add_argument("hcp_id", help="HCP ID (e.g. HCP_357811)")
    parser.add_argument("--year", type=int, default=None, help="Filter to a single year")
    args = parser.parse_args()
    main(args.hcp_id, args.year)
