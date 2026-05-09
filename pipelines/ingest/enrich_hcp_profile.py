# Copyright (c) 2026 Divya Rajaraman
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

"""
pipelines/ingest/enrich_hcp_profile.py — Enrich feature store with specialty,
state, and primary_rep_id from processed source data.

Run AFTER the main feature pipeline:
  python features/feature_store.py
  python pipelines/ingest/enrich_hcp_profile.py

Inputs:
  data/processed/hcp_master.parquet       — specialty, state
  data/processed/hcp_interactions.parquet — rep_id (most frequent per HCP)
  features/outputs/feature_store_raw.parquet  (read + overwrite)
  features/outputs/feature_store.parquet      (read + overwrite)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# ── Paths ─────────────────────────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parents[2]

_HCP_MASTER      = _ROOT / "data" / "processed" / "hcp_master.parquet"
_HCP_INTERACTIONS = _ROOT / "data" / "processed" / "hcp_interactions.parquet"
_FEATURE_RAW     = _ROOT / "features" / "outputs" / "feature_store_raw.parquet"
_FEATURE_STORE   = _ROOT / "features" / "outputs" / "feature_store.parquet"
_RISK_SCORES     = _ROOT / "models" / "outputs" / "risk_scores.parquet"


# ── Step 1 — Build profile table ──────────────────────────────────────────────

def build_profile(hcp_master_path: Path) -> pd.DataFrame:
    """Load specialty and state from hcp_master."""
    print("Step 1 — Loading HCP profile (specialty, state)…")
    master = pd.read_parquet(hcp_master_path, columns=["hcp_id", "specialty", "state"])
    profile = master[["hcp_id", "specialty", "state"]].copy()
    print(f"  Loaded {len(profile):,} HCP profile rows")
    return profile


# ── Step 2 — Build primary rep map ────────────────────────────────────────────

def build_rep_map(interactions_path: Path) -> pd.DataFrame:
    """Derive primary rep per HCP as the most frequently appearing rep_id."""
    print("Step 2 — Deriving primary_rep_id (most frequent rep per HCP)…")
    interactions = pd.read_parquet(interactions_path, columns=["hcp_id", "rep_id"])
    interactions = interactions.dropna(subset=["rep_id"])

    rep_map = (
        interactions
        .groupby(["hcp_id", "rep_id"])
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
        .drop_duplicates("hcp_id")
        [["hcp_id", "rep_id"]]
        .rename(columns={"rep_id": "primary_rep_id"})
    )
    print(f"  Mapped primary_rep_id for {len(rep_map):,} HCPs")
    return rep_map


# ── Step 3/4 — Enrich a single parquet ───────────────────────────────────────

def enrich_parquet(
    path: Path,
    profile: pd.DataFrame,
    rep_map: pd.DataFrame,
    label: str,
) -> None:
    """
    Load parquet at path, drop existing specialty/state/primary_rep_id if
    present, left-join profile and rep_map on hcp_id, save back in place.
    """
    print(f"Step {'3' if 'raw' in path.name else '4'} — Enriching {label}…")
    df = pd.read_parquet(path)
    if df.index.name == "hcp_id" or "hcp_id" not in df.columns:
        df = df.reset_index()
    original_len = len(df)
    print(f"  Loaded {original_len:,} rows")

    # Drop stale enrichment columns if already present
    drop_cols = [c for c in ("specialty", "state", "primary_rep_id") if c in df.columns]
    if drop_cols:
        df = df.drop(columns=drop_cols)
        print(f"  Dropped existing columns: {drop_cols}")

    # Left join — keep all HCPs
    df = df.merge(profile, on="hcp_id", how="left")
    df = df.merge(rep_map,  on="hcp_id", how="left")

    assert len(df) == original_len, (
        f"Row count changed after merge: {original_len} → {len(df)}"
    )

    df.to_parquet(path, index=False)
    print(f"  Saved {len(df):,} rows → {path.relative_to(_ROOT)}")

    # Summary nulls
    print(f"  specialty null:      {df['specialty'].isna().sum():,}")
    print(f"  state null:          {df['state'].isna().sum():,}")
    print(f"  primary_rep_id null: {df['primary_rep_id'].isna().sum():,}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("HCP Profile Enrichment")
    print("=" * 60)

    profile = build_profile(_HCP_MASTER)
    rep_map = build_rep_map(_HCP_INTERACTIONS)

    enrich_parquet(_FEATURE_RAW, profile, rep_map, "feature_store_raw.parquet")

    # Step 4 SKIPPED: feature_store.parquet is ML-only and must not contain
    # string columns (specialty/state/primary_rep_id). Those fields are
    # enriched into risk_scores.parquet only (which the API reads).
    print('Step 4 Skipped (feature_store.parquet is ML-only by design)')

    # Step 5 — risk_scores.parquet has hcp_id as a column
    print("Step 5 — Enriching risk_scores.parquet…")
    df = pd.read_parquet(_RISK_SCORES)
    print(f"  Loaded {len(df):,} rows")

    for col in ("specialty", "state", "primary_rep_id"):
        if col in df.columns:
            df = df.drop(columns=[col])

    df = df.merge(profile, on="hcp_id", how="left")
    df = df.merge(rep_map,  on="hcp_id", how="left")

    assert len(df) == 97011, (
        f"Row count changed after merge: expected 97011, got {len(df)}"
    )

    df.to_parquet(_RISK_SCORES, index=False)
    print(f"  Saved {len(df):,} rows → {_RISK_SCORES.relative_to(_ROOT)}")
    print(f"  specialty null:      {df['specialty'].isna().sum():,}")
    print(f"  state null:          {df['state'].isna().sum():,}")
    print(f"  primary_rep_id null: {df['primary_rep_id'].isna().sum():,}")
    print("Step 5 complete — risk_scores.parquet enriched")

    print("\nEnrichment complete")


if __name__ == "__main__":
    main()
