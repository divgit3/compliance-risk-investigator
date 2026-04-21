"""
features/industry_benchmarks.py — Industry Benchmark Computation (Task 3.5)

Loads competitor and population payment data from Athena (or falls back to
local hcp_spend_raw_dollars.parquet in dev), then computes per-HCP:
  - SOW (share of wallet vs competitors)
  - industry_ratio (Nova spend vs CMS population avg)
  - engagement_priority_score_full (full 100pts, vs prior 45pt cap)

Outputs:
  features/outputs/competitor_benchmarks.parquet
  features/outputs/population_benchmarks.parquet

Usage:
    python features/industry_benchmarks.py

Athena fallback:
  When Athena is not reachable (dev environment), the script falls back to
  hcp_spend_raw_dollars.parquet for population-level stats.  SOW is set to
  NaN (requires competitor data) and engagement_priority_score_full reflects
  only the industry_ratio component (up to 70pts from ratio+base vs 100 full).
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────────────

_ROOT       = Path(__file__).resolve().parent.parent
_OUTPUT_DIR = _ROOT / "features" / "outputs"
_SPEND_PATH = _OUTPUT_DIR / "hcp_spend_raw_dollars.parquet"

COMPETITOR_OUT  = _OUTPUT_DIR / "competitor_benchmarks.parquet"
POPULATION_OUT  = _OUTPUT_DIR / "population_benchmarks.parquet"

# ── Athena config ──────────────────────────────────────────────────────────────

_ATHENA_DB      = os.environ.get("ATHENA_DATABASE",    "compliance_risk_raw")
_ATHENA_BUCKET  = os.environ.get("ATHENA_S3_BUCKET",   "s3://compliance-risk-investigator/athena-query-output/")
_ATHENA_REGION  = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

# ── Score weights ──────────────────────────────────────────────────────────────

_SOW_MAX_PTS        = 40.0   # (1 - SOW) * 40
_INDUSTRY_MAX_PTS   = 30.0   # min(ratio, 2) / 2 * 30
_BASE_MAX_PTS       = 30.0   # NP rank (20) + persistence (10)


# ── Athena loaders (best-effort) ───────────────────────────────────────────────

def _athena_load_competitor() -> pd.DataFrame | None:
    """
    Load mart_competitor_payments from Athena.
    Returns None if awswrangler is not installed or Athena is not reachable.

    Expected schema (returns at minimum):
        hcp_id: str
        competitor_spend_2024: float   — total 2024 spend across all competitors
        competitor_avg_spend:  float   — specialty-adjusted competitor avg
    """
    try:
        import awswrangler as wr  # optional dependency
        logger.info("Querying Athena: mart_competitor_payments")
        df = wr.athena.read_sql_query(
            sql="""
                SELECT
                    hcp_id,
                    SUM(payment_amount) FILTER (WHERE program_year = 2024)
                        AS competitor_spend_2024,
                    AVG(payment_amount) FILTER (WHERE program_year = 2024)
                        AS competitor_avg_spend
                FROM mart_competitor_payments
                WHERE program_year = 2024
                GROUP BY hcp_id
            """,
            database=_ATHENA_DB,
            s3_output=_ATHENA_BUCKET,
            boto3_session=None,
        )
        df["hcp_id"] = df["hcp_id"].astype(str)
        logger.info("Athena competitor payments: {} rows", len(df))
        return df
    except ImportError:
        logger.warning("awswrangler not installed — skipping Athena competitor load")
        return None
    except Exception as exc:
        logger.warning("Athena competitor load failed: {} — using fallback", exc)
        return None


def _athena_load_population() -> pd.DataFrame | None:
    """
    Load mart_population_payments from Athena.
    Returns None if awswrangler is not installed or Athena is not reachable.

    Expected schema:
        specialty: str
        population_avg_spend_2024: float   — CMS avg spend for this specialty
        population_p90_spend_2024: float   — 90th percentile
    """
    try:
        import awswrangler as wr
        logger.info("Querying Athena: mart_population_payments")
        df = wr.athena.read_sql_query(
            sql="""
                SELECT
                    physician_specialty AS specialty,
                    AVG(yearly_spend) AS population_avg_spend_2024,
                    APPROX_PERCENTILE(yearly_spend, 0.90) AS population_p90_spend_2024
                FROM (
                    SELECT
                        hcp_id,
                        physician_specialty,
                        SUM(payment_amount) AS yearly_spend
                    FROM mart_population_payments
                    WHERE program_year = 2024
                    GROUP BY hcp_id, physician_specialty
                )
                GROUP BY physician_specialty
            """,
            database=_ATHENA_DB,
            s3_output=_ATHENA_BUCKET,
            boto3_session=None,
        )
        logger.info("Athena population payments: {} specialties", len(df))
        return df
    except ImportError:
        logger.warning("awswrangler not installed — skipping Athena population load")
        return None
    except Exception as exc:
        logger.warning("Athena population load failed: {} — using fallback", exc)
        return None


# ── Fallback: population stats from local parquet ─────────────────────────────

def _local_population_avg(nova_df: pd.DataFrame) -> pd.Series:
    """
    Derive per-HCP population_avg_spend from the Nova spend dataset itself
    (fallback when Athena is not available).

    Groups by specialty when available; falls back to overall mean when
    specialty is null/unknown.  Overall mean represents CMS population
    context as well as the dataset can approximate it.
    """
    spend = nova_df["spend_2024"].fillna(0.0)

    has_specialty = (
        "specialty" in nova_df.columns
        and nova_df["specialty"].notna().any()
    )
    if has_specialty:
        specialty = nova_df["specialty"].fillna("Unknown")
        pop_avg = (
            pd.DataFrame({"specialty": specialty, "spend": spend})
            .groupby("specialty")["spend"]
            .mean()
        )
        return specialty.map(pop_avg).fillna(spend.mean())
    else:
        overall_mean = spend.mean()
        return pd.Series(overall_mean, index=nova_df.index)


# ── Core computation ──────────────────────────────────────────────────────────

def compute_competitor_benchmarks(
    nova_df:       pd.DataFrame,
    competitor_df: pd.DataFrame | None,
) -> pd.DataFrame:
    """
    Compute per-HCP competitor benchmarks and SOW.

    Parameters
    ----------
    nova_df : DataFrame with columns [hcp_id, spend_2024]
    competitor_df : Athena result or None

    Returns
    -------
    DataFrame with columns:
        hcp_id, nova_spend_2024, competitor_spend,
        competitor_avg_spend, sow, athena_available
    """
    out = pd.DataFrame({
        "hcp_id":          nova_df["hcp_id"],
        "nova_spend_2024": nova_df["spend_2024"].fillna(0.0),
    })

    if competitor_df is not None:
        merged = out.merge(competitor_df, on="hcp_id", how="left")
        merged["competitor_spend"]     = merged["competitor_spend_2024"].fillna(0.0)
        merged["competitor_avg_spend"] = merged["competitor_avg_spend"].fillna(0.0)
        total = merged["nova_spend_2024"] + merged["competitor_spend"]
        merged["sow"] = np.where(
            total > 0,
            merged["nova_spend_2024"] / total,
            np.nan,
        )
        merged["athena_available"] = True
        return merged[["hcp_id", "nova_spend_2024", "competitor_spend",
                        "competitor_avg_spend", "sow", "athena_available"]]
    else:
        out["competitor_spend"]     = np.nan
        out["competitor_avg_spend"] = np.nan
        out["sow"]                  = np.nan
        out["athena_available"]     = False
        return out


def compute_population_benchmarks(
    nova_df:          pd.DataFrame,
    competitor_bm_df: pd.DataFrame,
    population_df:    pd.DataFrame | None,
) -> pd.DataFrame:
    """
    Compute per-HCP population benchmarks and full engagement_priority_score.

    Score formula (100pts total):
        sow_component      = (1 - SOW) * 40        — 40pts max (0 if Athena unavailable)
        industry_component = min(ratio, 2) / 2 * 30 — 30pts max
        base_component     = min(NP_rank * 20 + persistence * 10, 30) — 30pts max
          where:
            NP_rank    = percentile rank within local population
            persistence = min(outlier_years_count, 3) / 3

    Parameters
    ----------
    nova_df          : DataFrame with [hcp_id, spend_2024]
    competitor_bm_df : output of compute_competitor_benchmarks()
    population_df    : Athena specialty-level averages or None

    Returns
    -------
    DataFrame with per-HCP population benchmark and full EPS.
    """
    df = nova_df[["hcp_id"]].copy()
    nova_spend = nova_df["spend_2024"].fillna(0.0).values

    # ── Population average ────────────────────────────────────────────────────
    if population_df is not None and "population_avg_spend_2024" in population_df.columns:
        # Specialty-level averages from Athena
        if "specialty" in nova_df.columns:
            spec = nova_df["specialty"].fillna("Unknown")
            pop_map = population_df.set_index("specialty")["population_avg_spend_2024"]
            population_avg_spend = spec.map(pop_map).fillna(
                population_df["population_avg_spend_2024"].mean()
            ).values
        else:
            pop_global = float(population_df["population_avg_spend_2024"].mean())
            population_avg_spend = np.full(len(nova_df), pop_global)
    else:
        # Fallback: derive from local Nova dataset
        population_avg_spend = _local_population_avg(nova_df).values

    df["nova_spend_2024"]     = nova_spend
    df["population_avg_spend"] = population_avg_spend

    # ── Industry ratio ────────────────────────────────────────────────────────
    df["industry_ratio"] = np.where(
        population_avg_spend > 0,
        np.minimum(10.0, nova_spend / population_avg_spend),
        0.0,
    )

    # ── SOW component (0 if Athena not available) ─────────────────────────────
    sow = competitor_bm_df.set_index("hcp_id").reindex(nova_df["hcp_id"])["sow"].values
    athena_available = bool(competitor_bm_df["athena_available"].any())

    sow_component = np.where(
        ~np.isnan(sow),
        (1.0 - np.nan_to_num(sow, nan=0.0)) * _SOW_MAX_PTS,
        0.0,
    )

    # ── Industry component ────────────────────────────────────────────────────
    industry_component = np.minimum(df["industry_ratio"].values, 2.0) / 2.0 * _INDUSTRY_MAX_PTS

    # ── Base component: NP percentile rank + persistence ─────────────────────
    # Percentile rank within full local population (0–1 scale)
    from scipy.stats import rankdata  # scipy in requirements
    np_rank_pct = rankdata(nova_spend, method="min") / len(nova_spend)  # 0–1

    # Count years with above-median spend as a persistence proxy
    years_cols = [c for c in nova_df.columns if c.startswith("spend_20") and c != "spend_2024"]
    if years_cols:
        median_spend = np.median(nova_spend[nova_spend > 0]) if (nova_spend > 0).any() else 0
        outlier_count = np.sum(
            np.stack([(nova_df[c].fillna(0.0).values > median_spend) for c in years_cols], axis=1),
            axis=1,
        ).astype(float)
    else:
        outlier_count = np.zeros(len(nova_df))

    base_component = np.minimum(
        _BASE_MAX_PTS,
        np_rank_pct * 20.0 + np.minimum(outlier_count, 3.0) / 3.0 * 10.0,
    )

    # ── Full score ────────────────────────────────────────────────────────────
    full_score = np.minimum(100.0, sow_component + industry_component + base_component)

    df["sow_component"]                  = sow_component
    df["industry_component"]             = industry_component
    df["base_component"]                 = base_component
    df["engagement_priority_score_full"] = full_score
    df["athena_available"]               = athena_available

    logger.info(
        "EPS full — mean={:.1f}  max={:.1f}  athena_available={}",
        float(np.mean(full_score)),
        float(np.max(full_score)),
        athena_available,
    )
    return df


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("Task 3.5 — Industry Benchmark Computation")
    logger.info("Output dir: {}", _OUTPUT_DIR)

    # ── Load Nova spend (always available) ────────────────────────────────────
    if not _SPEND_PATH.exists():
        raise FileNotFoundError(f"Nova spend parquet not found: {_SPEND_PATH}")
    nova_df = pd.read_parquet(_SPEND_PATH)
    nova_df["hcp_id"] = nova_df["hcp_id"].astype(str)
    logger.info("Nova spend loaded: {} HCPs", len(nova_df))

    # ── Try Athena ────────────────────────────────────────────────────────────
    competitor_df = _athena_load_competitor()
    population_df = _athena_load_population()

    if competitor_df is None and population_df is None:
        logger.warning(
            "Athena not reachable — using local fallback. "
            "SOW will be NaN; EPS capped at ~70pts (no SOW component)."
        )

    # ── Compute ───────────────────────────────────────────────────────────────
    competitor_bm = compute_competitor_benchmarks(nova_df, competitor_df)
    population_bm = compute_population_benchmarks(nova_df, competitor_bm, population_df)

    # ── Save ──────────────────────────────────────────────────────────────────
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    competitor_bm.to_parquet(COMPETITOR_OUT, index=False)
    logger.info("Saved: {} ({} rows)", COMPETITOR_OUT.name, len(competitor_bm))

    population_bm.to_parquet(POPULATION_OUT, index=False)
    logger.info("Saved: {} ({} rows)", POPULATION_OUT.name, len(population_bm))

    # ── Summary ───────────────────────────────────────────────────────────────
    athena_used = bool(competitor_bm["athena_available"].any())
    eps_full    = population_bm["engagement_priority_score_full"]
    logger.info("─" * 60)
    logger.info("Athena used:          {}", athena_used)
    logger.info("EPS full — mean:      {:.2f}", eps_full.mean())
    logger.info("EPS full — max:       {:.2f}", eps_full.max())
    logger.info("EPS full — p90:       {:.2f}", eps_full.quantile(0.90))
    logger.info("HCPs with SOW:        {}", competitor_bm["sow"].notna().sum())
    logger.info("Population avg spend: {:.2f}", population_bm["population_avg_spend"].mean())
    if not athena_used:
        logger.warning(
            "data_limitations: Athena not reachable — competitor benchmarks "
            "unavailable, engagement_priority_score capped at 45pts"
        )


if __name__ == "__main__":
    main()
