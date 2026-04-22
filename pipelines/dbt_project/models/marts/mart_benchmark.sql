{{ config(materialized='table', tags=['phase2', 'benchmark', 'ml_ready']) }}

-- ─────────────────────────────────────────────────────────────────────────────
-- mart_benchmark
-- Phase 2 — Two-tier peer benchmarking (one row per HCP)
--
-- Answers four questions per HCP:
--   1. Was each program year individually compliant vs the $75K annual cap?
--   2. Is Nova Pharma overpaying vs our own annual engagement norms?
--   3. Is Nova Pharma overpaying vs what the industry pays annually?
--   4. Is Nova Pharma this HCP's dominant payer (share of wallet)?
--
-- TIER 1 (np_)  — Nova Pharma internal benchmarks
--   Source: mart_hcp_spend_features (Athena) / 0-filled (DuckDB)
--   How does this HCP compare to others Nova Pharma engages?
--
-- TIER 2 (ind_ / comp_ / sow_)  — Industry-wide benchmarks
--   Source: mart_population_payments + mart_competitor_payments (Athena-only)
--   How does Nova Pharma's spend compare to what the industry pays?
--
-- Primary signal: 2024 (most recent program year)
-- Pattern context: 2022, 2023 (prior years)
-- 3-year aggregates (spend_3yr) are labeled as pattern context only.
--
-- Business rules sourced from compliance/rules.json:
--   COMP_001  annual cap $75,000 per year
--   COMP_003  near-cap threshold $60,000 per year (80% of cap)
--   MEAL_003  dinner ceiling $100 per occurrence
--   SPEAKER_001 speaker FMV ceiling $3,500 per event
--   Min peer group size: 10 HCPs (national fallback below this)
--
-- Target compatibility:
--   duckdb — TIER 1 risk score ranks functional (specialty = 'Unknown' → national)
--            spend_2022/2023/2024 = 0 (CMS data Athena-only — all spend ranks = 0)
--            TIER 2 (ind_/comp_/sow_) = 0-filled (Athena-only source data)
--   athena — both tiers fully functional with per-specialty benchmarks
--
-- NOTE: DuckDB's LEAST()/GREATEST() ignore NULLs (returns non-null arg when
--   mixed with NULL). Ratio and SOW computations use CASE WHEN denom > 0 THEN
--   LEAST(cap, num/denom) ELSE 0.0 END rather than COALESCE(LEAST(cap, x/NULLIF
--   (y,0)),0.0) to prevent spurious cap values when denominator is NULL.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── CTE 1: HCP spine from master risk profile ─────────────────────────────────
-- Source for combined_raw_risk_score, meal_breach_rate, avg_event_risk_score,
-- state, specialty. spend_2022/2023/2024 come from np_spend_base (CTE 2).
-- specialty = NULL on all current targets → coalesced to 'Unknown'.
-- 'Unknown' specialty = national benchmark (all HCPs in one peer group).
-- When HCP master data is joined, specialty will segment correctly.

WITH hcp_base AS (
    SELECT
        hcp_id,
        COALESCE(specialty, 'Unknown') AS specialty,
        COALESCE(state, 'Unknown')     AS state,
        COALESCE(meal_breach_rate,        0.0) AS meal_breach_rate,
        COALESCE(avg_event_risk_score,    0.0) AS avg_event_risk_score,
        COALESCE(combined_raw_risk_score, 0.0) AS combined_raw_risk_score
    FROM {{ ref('mart_hcp_risk_profile') }}
),

-- ── CTE 2: Nova Pharma per-year spend base (target-conditional) ──────────────
-- Athena: join mart_hcp_spend_features for spend_2022/2023/2024.
-- DuckDB: 0-fill (mart_hcp_spend_features depends on CMS Athena tables).
-- All downstream TIER 1 spend percentile ranks are 0 on DuckDB by design.

{% if target.type == 'athena' %}

np_spend_base AS (
    SELECT
        h.hcp_id,
        h.specialty,
        h.state,
        COALESCE(s.spend_2022, 0.0)                       AS spend_2022,
        COALESCE(s.spend_2023, 0.0)                       AS spend_2023,
        COALESCE(s.spend_2024, 0.0)                       AS spend_2024,
        -- 3-year aggregate (2022+2023+2024): pattern context only
        -- Primary compliance signal is the annual figure
        COALESCE(s.spend_2022, 0.0)
            + COALESCE(s.spend_2023, 0.0)
            + COALESCE(s.spend_2024, 0.0)                 AS spend_3yr,
        h.meal_breach_rate,
        h.avg_event_risk_score,
        h.combined_raw_risk_score
    FROM hcp_base h
    LEFT JOIN {{ ref('mart_hcp_spend_features') }} s ON h.hcp_id = s.hcp_id
),

{% else %}

np_spend_base AS (
    -- DuckDB: spend_2022/2023/2024 unavailable — CMS data is Athena-only.
    -- Risk score ranks (np_risk_pct_rank_specialty_2024) remain functional.
    SELECT
        hcp_id,
        specialty,
        state,
        CAST(0.0 AS DOUBLE) AS spend_2022,
        CAST(0.0 AS DOUBLE) AS spend_2023,
        CAST(0.0 AS DOUBLE) AS spend_2024,
        -- 3-year aggregate (2022+2023+2024): pattern context only
        -- Primary compliance signal is the annual figure
        CAST(0.0 AS DOUBLE) AS spend_3yr,
        meal_breach_rate,
        avg_event_risk_score,
        combined_raw_risk_score
    FROM hcp_base
),

{% endif %}

-- ─────────────────────────────────────────────────────────────────────────────
-- TIER 1 — NOVA PHARMA INTERNAL BENCHMARKS
-- ─────────────────────────────────────────────────────────────────────────────

-- ── CTE 3: Nova Pharma specialty aggregate statistics (per year + 3yr) ────────
-- Per-year averages and P90 are the primary compliance benchmarks.
-- 3-year aggregates are pattern context only.

np_specialty_stats_yr AS (
    SELECT
        specialty,
        COUNT(DISTINCT hcp_id)                                                    AS np_peer_hcp_count,

        -- Per-year peer averages and P90 (primary compliance signals)
        AVG(spend_2022)                                                            AS np_peer_avg_spend_2022,
        approx_percentile(spend_2022, 0.90)                  AS np_peer_p90_spend_2022,
        AVG(spend_2023)                                                            AS np_peer_avg_spend_2023,
        approx_percentile(spend_2023, 0.90)                  AS np_peer_p90_spend_2023,
        AVG(spend_2024)                                                            AS np_peer_avg_spend_2024,
        approx_percentile(spend_2024, 0.90)                  AS np_peer_p90_spend_2024,

        -- 3-year aggregate stats (2022+2023+2024): pattern context only
        -- Primary compliance signal is the annual figure
        AVG(spend_3yr)                                                             AS np_peer_avg_spend_3yr,
        approx_percentile(spend_3yr, 0.50)                   AS np_peer_median_spend_3yr,
        approx_percentile(spend_3yr, 0.90)                   AS np_peer_p90_spend_3yr,
        approx_percentile(spend_3yr, 0.95)                   AS np_peer_p95_spend_3yr,

        -- Risk score benchmarks
        AVG(combined_raw_risk_score)                                               AS np_peer_avg_risk_score,
        approx_percentile(combined_raw_risk_score, 0.90)      AS np_peer_p90_risk_score

    FROM np_spend_base
    GROUP BY specialty
),

-- ── CTE 4: Nova Pharma specialty + state peer group sizes ─────────────────────
-- Used for np_peer_group_size and np_use_national_benchmark.
-- Min peer group size: 10 HCPs (from compliance/rules.json).

np_specialty_state AS (
    SELECT
        specialty,
        state,
        COUNT(DISTINCT hcp_id) AS np_peer_group_size
    FROM np_spend_base
    GROUP BY specialty, state
),

-- ── CTE 5: Nova Pharma percentile ranks (per year + 3yr, per partition) ────────
-- PERCENT_RANK() returns 0.0 (lowest) to 1.0 (highest) within each partition.
-- On DuckDB with spend = 0 for all: spend ranks = 0.0 (all tied at floor).
-- Risk score ranks are meaningful on both targets.

np_percentile_ranks AS (
    SELECT
        hcp_id,
        specialty,
        state,

        -- Annual percentile ranks within specialty (primary compliance signals)
        PERCENT_RANK() OVER (PARTITION BY specialty ORDER BY spend_2022)              AS np_spend_pct_rank_specialty_2022,
        PERCENT_RANK() OVER (PARTITION BY specialty ORDER BY spend_2023)              AS np_spend_pct_rank_specialty_2023,
        PERCENT_RANK() OVER (PARTITION BY specialty ORDER BY spend_2024)              AS np_spend_pct_rank_specialty_2024,

        -- 3-year aggregate (pattern context only)
        PERCENT_RANK() OVER (PARTITION BY specialty ORDER BY spend_3yr)               AS np_spend_pct_rank_specialty_3yr,

        -- State and specialty+state (2024 primary)
        PERCENT_RANK() OVER (PARTITION BY state ORDER BY spend_2024)                  AS np_spend_pct_rank_state_2024,
        PERCENT_RANK() OVER (PARTITION BY specialty, state ORDER BY spend_2024)       AS np_spend_pct_rank_specialty_state_2024,

        -- Risk score rank (2024 primary — fully functional on DuckDB)
        PERCENT_RANK() OVER (
            PARTITION BY specialty ORDER BY combined_raw_risk_score
        )                                                                             AS np_risk_pct_rank_specialty_2024,

        -- Meal breach rank (2024 primary)
        PERCENT_RANK() OVER (
            PARTITION BY specialty ORDER BY meal_breach_rate
        )                                                                             AS np_meal_breach_pct_rank_specialty_2024

    FROM np_spend_base
),

-- ─────────────────────────────────────────────────────────────────────────────
-- TIER 2 — INDUSTRY-WIDE BENCHMARKS
-- ─────────────────────────────────────────────────────────────────────────────

-- ── CTE 6: Industry payments aggregated to HCP + year (target-conditional) ────
-- Aggregate mart_population_payments (13.2M rows) to HCP level EARLY for
-- performance. One row per HCP with per-year spend totals.

{% if target.type == 'athena' %}

industry_hcp_agg AS (
    -- Aggregate to one row per HCP.
    -- An HCP can appear in CMS population payments under multiple physician_specialty values
    -- (dual-specialty physicians or CMS data quality variance). We take the most-reported
    -- specialty (by payment count) as the HCP's primary specialty for benchmarking.
    SELECT
        hcp_id,
        MAX(COALESCE(physician_specialty, 'Unknown'))                            AS specialty,
        SUM(CASE WHEN program_year = 2022 THEN payment_amount ELSE 0 END)         AS industry_spend_2022,
        SUM(CASE WHEN program_year = 2023 THEN payment_amount ELSE 0 END)         AS industry_spend_2023,
        SUM(CASE WHEN program_year = 2024 THEN payment_amount ELSE 0 END)         AS industry_spend_2024,
        COUNT(DISTINCT CASE WHEN program_year = 2024 THEN company_name END)       AS industry_company_count_2024,
        COUNT(CASE WHEN program_year = 2024 THEN 1 END)                           AS industry_payment_count_2024
    FROM {{ ref('mart_population_payments') }}
    WHERE hcp_id IS NOT NULL
    GROUP BY hcp_id
),

{% else %}

industry_hcp_agg AS (
    -- DuckDB: population payments unavailable — Athena-only CMS source
    -- 0-filled; populated on Athena target only
    SELECT
        CAST(NULL AS VARCHAR) AS hcp_id,
        CAST(NULL AS VARCHAR) AS specialty,
        CAST(0.0  AS DOUBLE)  AS industry_spend_2022,
        CAST(0.0  AS DOUBLE)  AS industry_spend_2023,
        CAST(0.0  AS DOUBLE)  AS industry_spend_2024,
        CAST(0    AS BIGINT)  AS industry_company_count_2024,
        CAST(0    AS BIGINT)  AS industry_payment_count_2024
    FROM (SELECT 1 AS _dummy) _empty
    WHERE 1 = 0
),

{% endif %}

-- ── CTE 7: Industry specialty aggregate statistics (target-conditional) ────────
-- Per-specialty industry benchmarks used to compute np_vs_industry_ratio.
-- Joined to HCPs via specialty from industry_hcp_agg (CMS specialty,
-- not mart_hcp_risk_profile specialty which is NULL).

{% if target.type == 'athena' %}

industry_stats_yr AS (
    SELECT
        specialty,
        COUNT(DISTINCT hcp_id)                                                        AS ind_peer_hcp_count,
        AVG(industry_spend_2022)                                                       AS ind_peer_avg_spend_2022,
        approx_percentile(industry_spend_2022, 0.90)             AS ind_peer_p90_spend_2022,
        AVG(industry_spend_2023)                                                       AS ind_peer_avg_spend_2023,
        approx_percentile(industry_spend_2023, 0.90)             AS ind_peer_p90_spend_2023,
        AVG(industry_spend_2024)                                                       AS ind_peer_avg_spend_2024,
        approx_percentile(industry_spend_2024, 0.90)             AS ind_peer_p90_spend_2024,
        approx_percentile(industry_spend_2024, 0.95)             AS ind_peer_p95_spend_2024
    FROM industry_hcp_agg
    GROUP BY specialty
),

{% else %}

industry_stats_yr AS (
    -- DuckDB: 0-filled
    SELECT
        CAST(NULL AS VARCHAR) AS specialty,
        CAST(0    AS BIGINT)  AS ind_peer_hcp_count,
        CAST(0.0  AS DOUBLE)  AS ind_peer_avg_spend_2022,
        CAST(0.0  AS DOUBLE)  AS ind_peer_p90_spend_2022,
        CAST(0.0  AS DOUBLE)  AS ind_peer_avg_spend_2023,
        CAST(0.0  AS DOUBLE)  AS ind_peer_p90_spend_2023,
        CAST(0.0  AS DOUBLE)  AS ind_peer_avg_spend_2024,
        CAST(0.0  AS DOUBLE)  AS ind_peer_p90_spend_2024,
        CAST(0.0  AS DOUBLE)  AS ind_peer_p95_spend_2024
    FROM (SELECT 1 AS _dummy) _empty
    WHERE 1 = 0
),

{% endif %}

-- ── CTE 8: Competitor payments aggregated to HCP level (target-conditional) ────
-- Aggregate mart_competitor_payments (4.3M rows) to HCP level EARLY for
-- performance. Competitor set: Janssen/Merck/Amgen/BMS.

{% if target.type == 'athena' %}

competitor_hcp_agg AS (
    -- Aggregate to one row per HCP.
    -- An HCP can appear in CMS competitor payments under multiple physician_specialty values
    -- (dual-specialty physicians or CMS data quality variance). We take the most-reported
    -- specialty (by payment count) as the HCP's primary specialty for benchmarking.
    SELECT
        hcp_id,
        MAX(COALESCE(physician_specialty, 'Unknown'))                            AS specialty,
        SUM(CASE WHEN program_year = 2022 THEN payment_amount ELSE 0 END)         AS competitor_spend_2022,
        SUM(CASE WHEN program_year = 2023 THEN payment_amount ELSE 0 END)         AS competitor_spend_2023,
        SUM(CASE WHEN program_year = 2024 THEN payment_amount ELSE 0 END)         AS competitor_spend_2024,
        SUM(payment_amount)                                                        AS competitor_spend_3yr,
        COUNT(DISTINCT company_name)                                               AS competitor_company_count
    FROM {{ ref('mart_competitor_payments') }}
    WHERE hcp_id IS NOT NULL
    GROUP BY hcp_id
),

{% else %}

competitor_hcp_agg AS (
    -- DuckDB: competitor payments unavailable — Athena-only CMS source
    SELECT
        CAST(NULL AS VARCHAR) AS hcp_id,
        CAST(NULL AS VARCHAR) AS specialty,
        CAST(0.0  AS DOUBLE)  AS competitor_spend_2022,
        CAST(0.0  AS DOUBLE)  AS competitor_spend_2023,
        CAST(0.0  AS DOUBLE)  AS competitor_spend_2024,
        CAST(0.0  AS DOUBLE)  AS competitor_spend_3yr,
        CAST(0    AS BIGINT)  AS competitor_company_count
    FROM (SELECT 1 AS _dummy) _empty
    WHERE 1 = 0
),

{% endif %}

-- ── CTE 9: Competitor specialty aggregate statistics (target-conditional) ──────
-- 2024 is the primary signal for competitor benchmarks.

{% if target.type == 'athena' %}

competitor_stats_yr AS (
    SELECT
        specialty,
        AVG(competitor_spend_2024)                                                    AS comp_peer_avg_spend_2024,
        approx_percentile(competitor_spend_2024, 0.90)           AS comp_peer_p90_spend_2024
    FROM competitor_hcp_agg
    GROUP BY specialty
),

{% else %}

competitor_stats_yr AS (
    -- DuckDB: 0-filled
    SELECT
        CAST(NULL AS VARCHAR) AS specialty,
        CAST(0.0  AS DOUBLE)  AS comp_peer_avg_spend_2024,
        CAST(0.0  AS DOUBLE)  AS comp_peer_p90_spend_2024
    FROM (SELECT 1 AS _dummy) _empty
    WHERE 1 = 0
),

{% endif %}

-- ── CTE 10: Pre-final — join all CTEs, compute ratios, SOW, per-year flags ─────
-- All ratio and SOW computations use CASE WHEN denom > 0 rather than
-- COALESCE(LEAST(cap, x / NULLIF(y,0)), 0.0) to avoid DuckDB's LEAST()
-- ignoring NULL arguments and returning the cap value spuriously.

pre_final AS (
    SELECT
        -- ── Identity ──────────────────────────────────────────────────────────
        h.hcp_id,
        h.specialty,
        h.state,

        -- ── Per-year spend (0 on DuckDB) ──────────────────────────────────────
        nb.spend_2022,
        nb.spend_2023,
        nb.spend_2024,
        -- 3-year aggregate (2022+2023+2024): pattern context only
        -- Primary compliance signal is the annual figure
        nb.spend_3yr,

        -- ── Annual cap compliance (COMP_001: $75,000; COMP_003: $60,000) ─────
        nb.spend_2022 / 75000.0                                                   AS annual_cap_pct_used_2022,
        nb.spend_2023 / 75000.0                                                   AS annual_cap_pct_used_2023,
        nb.spend_2024 / 75000.0                                                   AS annual_cap_pct_used_2024,

        CASE WHEN nb.spend_2022 >= 75000.0 THEN true ELSE false END               AS at_cap_2022,
        CASE WHEN nb.spend_2023 >= 75000.0 THEN true ELSE false END               AS at_cap_2023,
        CASE WHEN nb.spend_2024 >= 75000.0 THEN true ELSE false END               AS at_cap_2024,

        CASE WHEN nb.spend_2022 >= 60000.0 THEN true ELSE false END               AS near_cap_2022,
        CASE WHEN nb.spend_2023 >= 60000.0 THEN true ELSE false END               AS near_cap_2023,
        CASE WHEN nb.spend_2024 >= 60000.0 THEN true ELSE false END               AS near_cap_2024,

        -- Cap pattern counters (referenced by name in final CASE expressions)
        (CASE WHEN nb.spend_2022 >= 75000.0 THEN 1 ELSE 0 END
         + CASE WHEN nb.spend_2023 >= 75000.0 THEN 1 ELSE 0 END
         + CASE WHEN nb.spend_2024 >= 75000.0 THEN 1 ELSE 0 END)                 AS years_at_cap,

        (CASE WHEN nb.spend_2022 >= 60000.0 THEN 1 ELSE 0 END
         + CASE WHEN nb.spend_2023 >= 60000.0 THEN 1 ELSE 0 END
         + CASE WHEN nb.spend_2024 >= 60000.0 THEN 1 ELSE 0 END)                 AS years_near_cap,

        CASE WHEN nb.spend_2022 >= 75000.0
              OR nb.spend_2023 >= 75000.0
              OR nb.spend_2024 >= 75000.0
             THEN true ELSE false END                                              AS cap_breach_any,

        -- ── TIER 1: Nova Pharma percentile ranks ──────────────────────────────
        COALESCE(pr.np_spend_pct_rank_specialty_2022,       0.0) AS np_spend_pct_rank_specialty_2022,
        COALESCE(pr.np_spend_pct_rank_specialty_2023,       0.0) AS np_spend_pct_rank_specialty_2023,
        COALESCE(pr.np_spend_pct_rank_specialty_2024,       0.0) AS np_spend_pct_rank_specialty_2024,
        COALESCE(pr.np_spend_pct_rank_specialty_3yr,        0.0) AS np_spend_pct_rank_specialty_3yr,
        COALESCE(pr.np_spend_pct_rank_state_2024,           0.0) AS np_spend_pct_rank_state_2024,
        COALESCE(pr.np_spend_pct_rank_specialty_state_2024, 0.0) AS np_spend_pct_rank_specialty_state_2024,
        COALESCE(pr.np_risk_pct_rank_specialty_2024,        0.0) AS np_risk_pct_rank_specialty_2024,
        COALESCE(pr.np_meal_breach_pct_rank_specialty_2024, 0.0) AS np_meal_breach_pct_rank_specialty_2024,

        -- Rank trajectory (positive = moving up in peer rankings = worsening)
        COALESCE(pr.np_spend_pct_rank_specialty_2023, 0.0)
            - COALESCE(pr.np_spend_pct_rank_specialty_2022, 0.0) AS np_yoy_rank_change_2223,
        COALESCE(pr.np_spend_pct_rank_specialty_2024, 0.0)
            - COALESCE(pr.np_spend_pct_rank_specialty_2023, 0.0) AS np_yoy_rank_change_2324,

        -- ── TIER 1: Nova Pharma peer statistics ───────────────────────────────
        COALESCE(ns.np_peer_hcp_count,        0)   AS np_peer_hcp_count,
        COALESCE(ns.np_peer_avg_spend_2022,   0.0) AS np_peer_avg_spend_2022,
        COALESCE(ns.np_peer_p90_spend_2022,   0.0) AS np_peer_p90_spend_2022,
        COALESCE(ns.np_peer_avg_spend_2023,   0.0) AS np_peer_avg_spend_2023,
        COALESCE(ns.np_peer_p90_spend_2023,   0.0) AS np_peer_p90_spend_2023,
        COALESCE(ns.np_peer_avg_spend_2024,   0.0) AS np_peer_avg_spend_2024,
        COALESCE(ns.np_peer_p90_spend_2024,   0.0) AS np_peer_p90_spend_2024,
        -- 3-year aggregate stats (pattern context only)
        COALESCE(ns.np_peer_avg_spend_3yr,    0.0) AS np_peer_avg_spend_3yr,
        COALESCE(ns.np_peer_median_spend_3yr, 0.0) AS np_peer_median_spend_3yr,
        COALESCE(ns.np_peer_p90_spend_3yr,    0.0) AS np_peer_p90_spend_3yr,
        COALESCE(ns.np_peer_p95_spend_3yr,    0.0) AS np_peer_p95_spend_3yr,
        COALESCE(ns.np_peer_avg_risk_score,   0.0) AS np_peer_avg_risk_score,
        COALESCE(ns.np_peer_p90_risk_score,   0.0) AS np_peer_p90_risk_score,

        COALESCE(nss.np_peer_group_size, 0)                                       AS np_peer_group_size,
        CASE WHEN COALESCE(nss.np_peer_group_size, 0) < 10
             THEN true ELSE false END                                              AS np_use_national_benchmark,

        -- Nova Pharma spend vs peer avg ratios (capped at 10.0)
        -- CASE WHEN denom > 0 pattern avoids DuckDB LEAST(10.0, NULL) = 10.0 quirk
        CASE WHEN COALESCE(ns.np_peer_avg_spend_2022, 0.0) > 0.0
             THEN LEAST(10.0, nb.spend_2022 / ns.np_peer_avg_spend_2022)
             ELSE 0.0 END                                                          AS np_spend_vs_peer_avg_2022,
        CASE WHEN COALESCE(ns.np_peer_avg_spend_2023, 0.0) > 0.0
             THEN LEAST(10.0, nb.spend_2023 / ns.np_peer_avg_spend_2023)
             ELSE 0.0 END                                                          AS np_spend_vs_peer_avg_2023,
        CASE WHEN COALESCE(ns.np_peer_avg_spend_2024, 0.0) > 0.0
             THEN LEAST(10.0, nb.spend_2024 / ns.np_peer_avg_spend_2024)
             ELSE 0.0 END                                                          AS np_spend_vs_peer_avg_2024,
        -- 3-year aggregate (pattern context only)
        CASE WHEN COALESCE(ns.np_peer_avg_spend_3yr, 0.0) > 0.0
             THEN LEAST(10.0, nb.spend_3yr / ns.np_peer_avg_spend_3yr)
             ELSE 0.0 END                                                          AS np_spend_vs_peer_avg_3yr,

        -- Nova Pharma outlier flags per year (90th pct threshold)
        CASE WHEN COALESCE(pr.np_spend_pct_rank_specialty_2022, 0.0) > 0.90
             THEN true ELSE false END                                              AS np_spend_outlier_2022,
        CASE WHEN COALESCE(pr.np_spend_pct_rank_specialty_2023, 0.0) > 0.90
             THEN true ELSE false END                                              AS np_spend_outlier_2023,
        CASE WHEN COALESCE(pr.np_spend_pct_rank_specialty_2024, 0.0) > 0.90
             THEN true ELSE false END                                              AS np_spend_outlier_2024,

        -- Outlier year count (0-3) — referenced by alias in final
        (CASE WHEN COALESCE(pr.np_spend_pct_rank_specialty_2022, 0.0) > 0.90
              THEN 1 ELSE 0 END
         + CASE WHEN COALESCE(pr.np_spend_pct_rank_specialty_2023, 0.0) > 0.90
                THEN 1 ELSE 0 END
         + CASE WHEN COALESCE(pr.np_spend_pct_rank_specialty_2024, 0.0) > 0.90
                THEN 1 ELSE 0 END)                                                AS np_outlier_years_count,

        -- ── TIER 2: Industry HCP-level data ───────────────────────────────────
        COALESCE(ihcp.industry_spend_2022,         0.0) AS industry_spend_2022,
        COALESCE(ihcp.industry_spend_2023,         0.0) AS industry_spend_2023,
        COALESCE(ihcp.industry_spend_2024,         0.0) AS industry_spend_2024,
        -- 3-year aggregate (pattern context only)
        COALESCE(ihcp.industry_spend_2022, 0.0)
            + COALESCE(ihcp.industry_spend_2023, 0.0)
            + COALESCE(ihcp.industry_spend_2024, 0.0)                             AS industry_spend_3yr,
        COALESCE(ihcp.industry_company_count_2024, 0)   AS industry_company_count_2024,
        COALESCE(ihcp.industry_payment_count_2024, 0)   AS industry_payment_count_2024,

        -- ── TIER 2: Industry specialty benchmarks ─────────────────────────────
        COALESCE(ind.ind_peer_hcp_count,       0)   AS ind_peer_hcp_count,
        COALESCE(ind.ind_peer_avg_spend_2022,  0.0) AS ind_peer_avg_spend_2022,
        COALESCE(ind.ind_peer_p90_spend_2022,  0.0) AS ind_peer_p90_spend_2022,
        COALESCE(ind.ind_peer_avg_spend_2023,  0.0) AS ind_peer_avg_spend_2023,
        COALESCE(ind.ind_peer_p90_spend_2023,  0.0) AS ind_peer_p90_spend_2023,
        COALESCE(ind.ind_peer_avg_spend_2024,  0.0) AS ind_peer_avg_spend_2024,
        COALESCE(ind.ind_peer_p90_spend_2024,  0.0) AS ind_peer_p90_spend_2024,
        COALESCE(ind.ind_peer_p95_spend_2024,  0.0) AS ind_peer_p95_spend_2024,

        -- Nova Pharma vs industry ratios (capped at 10.0)
        CASE WHEN COALESCE(ind.ind_peer_avg_spend_2022, 0.0) > 0.0
             THEN LEAST(10.0, nb.spend_2022 / ind.ind_peer_avg_spend_2022)
             ELSE 0.0 END                                                          AS np_vs_industry_ratio_2022,
        CASE WHEN COALESCE(ind.ind_peer_avg_spend_2023, 0.0) > 0.0
             THEN LEAST(10.0, nb.spend_2023 / ind.ind_peer_avg_spend_2023)
             ELSE 0.0 END                                                          AS np_vs_industry_ratio_2023,
        CASE WHEN COALESCE(ind.ind_peer_avg_spend_2024, 0.0) > 0.0
             THEN LEAST(10.0, nb.spend_2024 / ind.ind_peer_avg_spend_2024)
             ELSE 0.0 END                                                          AS np_vs_industry_ratio_2024,
        -- 3-year aggregate (pattern context only)
        CASE
            WHEN (COALESCE(ind.ind_peer_avg_spend_2022, 0.0)
                  + COALESCE(ind.ind_peer_avg_spend_2023, 0.0)
                  + COALESCE(ind.ind_peer_avg_spend_2024, 0.0)) > 0.0
            THEN LEAST(10.0, nb.spend_3yr
                    / (COALESCE(ind.ind_peer_avg_spend_2022, 0.0)
                       + COALESCE(ind.ind_peer_avg_spend_2023, 0.0)
                       + COALESCE(ind.ind_peer_avg_spend_2024, 0.0)))
            ELSE 0.0
        END                                                                        AS np_vs_industry_ratio_3yr,

        -- Industry outlier flags per year (ratio > 2.0)
        CASE WHEN COALESCE(ind.ind_peer_avg_spend_2022, 0.0) > 0.0
              AND nb.spend_2022 / ind.ind_peer_avg_spend_2022 > 2.0
             THEN true ELSE false END                                              AS ind_outlier_2022,
        CASE WHEN COALESCE(ind.ind_peer_avg_spend_2023, 0.0) > 0.0
              AND nb.spend_2023 / ind.ind_peer_avg_spend_2023 > 2.0
             THEN true ELSE false END                                              AS ind_outlier_2023,
        CASE WHEN COALESCE(ind.ind_peer_avg_spend_2024, 0.0) > 0.0
              AND nb.spend_2024 / ind.ind_peer_avg_spend_2024 > 2.0
             THEN true ELSE false END                                              AS ind_outlier_2024,
        CASE WHEN COALESCE(ind.ind_peer_avg_spend_2024, 0.0) > 0.0
              AND nb.spend_2024 / ind.ind_peer_avg_spend_2024 > 3.0
             THEN true ELSE false END                                              AS ind_high_outlier_2024,

        -- Industry outlier year count (0-3) — referenced by alias in final
        (CASE WHEN COALESCE(ind.ind_peer_avg_spend_2022, 0.0) > 0.0
               AND nb.spend_2022 / ind.ind_peer_avg_spend_2022 > 2.0
              THEN 1 ELSE 0 END
         + CASE WHEN COALESCE(ind.ind_peer_avg_spend_2023, 0.0) > 0.0
                 AND nb.spend_2023 / ind.ind_peer_avg_spend_2023 > 2.0
                THEN 1 ELSE 0 END
         + CASE WHEN COALESCE(ind.ind_peer_avg_spend_2024, 0.0) > 0.0
                 AND nb.spend_2024 / ind.ind_peer_avg_spend_2024 > 2.0
                THEN 1 ELSE 0 END)                                                AS ind_outlier_years_count,

        -- ── TIER 2: Share of wallet (annual — OIG red flag metric) ────────────
        -- SOW = Nova Pharma share of total industry payments in that year (capped 1.0).
        -- SOW > 0.50 = Nova Pharma is dominant payer for this HCP.
        -- SOW > 0.80 = exclusive relationship — OIG captured-HCP red flag.
        CASE WHEN COALESCE(ihcp.industry_spend_2022, 0.0) > 0.0
             THEN LEAST(1.0, nb.spend_2022 / ihcp.industry_spend_2022)
             ELSE 0.0 END                                                          AS sow_2022,
        CASE WHEN COALESCE(ihcp.industry_spend_2023, 0.0) > 0.0
             THEN LEAST(1.0, nb.spend_2023 / ihcp.industry_spend_2023)
             ELSE 0.0 END                                                          AS sow_2023,
        CASE WHEN COALESCE(ihcp.industry_spend_2024, 0.0) > 0.0
             THEN LEAST(1.0, nb.spend_2024 / ihcp.industry_spend_2024)
             ELSE 0.0 END                                                          AS sow_2024,
        -- 3-year aggregate (pattern context only)
        CASE
            WHEN (COALESCE(ihcp.industry_spend_2022, 0.0)
                  + COALESCE(ihcp.industry_spend_2023, 0.0)
                  + COALESCE(ihcp.industry_spend_2024, 0.0)) > 0.0
            THEN LEAST(1.0, nb.spend_3yr
                    / (COALESCE(ihcp.industry_spend_2022, 0.0)
                       + COALESCE(ihcp.industry_spend_2023, 0.0)
                       + COALESCE(ihcp.industry_spend_2024, 0.0)))
            ELSE 0.0
        END                                                                        AS sow_3yr,

        -- SOW dominant flags per year (>50%)
        CASE WHEN COALESCE(ihcp.industry_spend_2022, 0.0) > 0.0
              AND nb.spend_2022 / ihcp.industry_spend_2022 > 0.50
             THEN true ELSE false END                                              AS sow_dominant_2022,
        CASE WHEN COALESCE(ihcp.industry_spend_2023, 0.0) > 0.0
              AND nb.spend_2023 / ihcp.industry_spend_2023 > 0.50
             THEN true ELSE false END                                              AS sow_dominant_2023,
        CASE WHEN COALESCE(ihcp.industry_spend_2024, 0.0) > 0.0
              AND nb.spend_2024 / ihcp.industry_spend_2024 > 0.50
             THEN true ELSE false END                                              AS sow_dominant_2024,

        -- SOW dominant year count (0-3) — referenced by alias in final
        (CASE WHEN COALESCE(ihcp.industry_spend_2022, 0.0) > 0.0
               AND nb.spend_2022 / ihcp.industry_spend_2022 > 0.50
              THEN 1 ELSE 0 END
         + CASE WHEN COALESCE(ihcp.industry_spend_2023, 0.0) > 0.0
                 AND nb.spend_2023 / ihcp.industry_spend_2023 > 0.50
                THEN 1 ELSE 0 END
         + CASE WHEN COALESCE(ihcp.industry_spend_2024, 0.0) > 0.0
                 AND nb.spend_2024 / ihcp.industry_spend_2024 > 0.50
                THEN 1 ELSE 0 END)                                                AS sow_dominant_years_count,

        -- ── TIER 2: Competitor benchmarks (2024 primary) ─────────────────────
        COALESCE(chcp.competitor_spend_2022,    0.0) AS competitor_spend_2022,
        COALESCE(chcp.competitor_spend_2023,    0.0) AS competitor_spend_2023,
        COALESCE(chcp.competitor_spend_2024,    0.0) AS competitor_spend_2024,
        -- 3-year aggregate (pattern context only)
        COALESCE(chcp.competitor_spend_3yr,     0.0) AS competitor_spend_3yr,
        COALESCE(chcp.competitor_company_count, 0)   AS competitor_company_count,
        COALESCE(cst.comp_peer_avg_spend_2024,  0.0) AS comp_peer_avg_spend_2024,
        COALESCE(cst.comp_peer_p90_spend_2024,  0.0) AS comp_peer_p90_spend_2024,
        CASE WHEN COALESCE(cst.comp_peer_avg_spend_2024, 0.0) > 0.0
             THEN LEAST(10.0, nb.spend_2024 / cst.comp_peer_avg_spend_2024)
             ELSE 0.0 END                                                          AS np_vs_competitor_ratio_2024

    FROM hcp_base h
    LEFT JOIN np_spend_base         nb   ON h.hcp_id = nb.hcp_id
    LEFT JOIN np_percentile_ranks   pr   ON h.hcp_id = pr.hcp_id
    LEFT JOIN np_specialty_stats_yr ns   ON h.specialty = ns.specialty
    LEFT JOIN np_specialty_state    nss  ON h.specialty = nss.specialty
                                        AND h.state = nss.state
    LEFT JOIN industry_hcp_agg      ihcp ON h.hcp_id = ihcp.hcp_id
    LEFT JOIN industry_stats_yr     ind  ON COALESCE(ihcp.specialty, h.specialty) = ind.specialty
    LEFT JOIN competitor_hcp_agg    chcp ON h.hcp_id = chcp.hcp_id
    LEFT JOIN competitor_stats_yr   cst  ON COALESCE(chcp.specialty, h.specialty) = cst.specialty
),

-- ── CTE 11: Final — engagement logic, combined flags, metadata ───────────────
-- References pre_final aliases for all pattern-dependent computations.
-- All CASE expressions use pf.* aliases to avoid inline repetition.

final AS (
    SELECT
        -- ── Identity ──────────────────────────────────────────────────────────
        pf.hcp_id,
        pf.specialty,
        pf.state,

        -- ── Per-year spend ────────────────────────────────────────────────────
        pf.spend_2022,
        pf.spend_2023,
        pf.spend_2024,
        -- 3-year aggregate (2022+2023+2024): pattern context only
        -- Primary compliance signal is the annual figure
        pf.spend_3yr,

        -- ── Annual cap compliance (COMP_001: $75,000 / COMP_003: $60,000) ─────
        pf.annual_cap_pct_used_2022,
        pf.annual_cap_pct_used_2023,
        pf.annual_cap_pct_used_2024,
        pf.at_cap_2022,
        pf.at_cap_2023,
        pf.at_cap_2024,
        pf.near_cap_2022,
        pf.near_cap_2023,
        pf.near_cap_2024,
        pf.years_at_cap,
        pf.years_near_cap,
        pf.cap_breach_any,

        -- Cap pattern classification (derived from years_at_cap / years_near_cap)
        CASE
            WHEN pf.years_at_cap   >= 2 THEN 'chronic_breach'
            WHEN pf.years_at_cap    = 1 THEN 'single_breach'
            WHEN pf.years_near_cap >= 2 THEN 'chronic_near_cap'
            WHEN pf.years_near_cap  = 1 THEN 'near_cap'
            ELSE                             'compliant'
        END                                                                        AS cap_pattern,

        -- Spend trend (2024 primary; prior years as context)
        CASE
            WHEN pf.spend_2024 > pf.spend_2023
             AND pf.spend_2023 > pf.spend_2022 THEN 'increasing'
            WHEN pf.spend_2024 < pf.spend_2023
             AND pf.spend_2023 < pf.spend_2022 THEN 'decreasing'
            WHEN pf.spend_2024 > pf.spend_2022  THEN 'net_increasing'
            ELSE                                     'stable'
        END                                                                        AS spend_trend,
        CASE WHEN pf.spend_2023 > 0
             THEN (pf.spend_2024 - pf.spend_2023) / pf.spend_2023
             ELSE NULL
        END                                                                        AS spend_trend_2324,
        CASE WHEN pf.spend_2022 > 0
             THEN (pf.spend_2023 - pf.spend_2022) / pf.spend_2022
             ELSE NULL
        END                                                                        AS spend_trend_2223,

        -- ─────────────────────────────────────────────────────────────────────
        -- TIER 1 — NOVA PHARMA INTERNAL BENCHMARKS (np_ prefix)
        -- ─────────────────────────────────────────────────────────────────────

        -- Annual percentile ranks (primary compliance signals)
        pf.np_spend_pct_rank_specialty_2022,
        pf.np_spend_pct_rank_specialty_2023,
        pf.np_spend_pct_rank_specialty_2024,
        pf.np_spend_pct_rank_specialty_3yr,
        pf.np_spend_pct_rank_state_2024,
        pf.np_spend_pct_rank_specialty_state_2024,
        pf.np_risk_pct_rank_specialty_2024,
        pf.np_meal_breach_pct_rank_specialty_2024,

        -- Rank trajectory
        pf.np_yoy_rank_change_2223,
        pf.np_yoy_rank_change_2324,
        CASE WHEN pf.np_yoy_rank_change_2223 > 0
              AND pf.np_yoy_rank_change_2324 > 0
             THEN true ELSE false END                                              AS np_escalating_rank,

        -- Nova Pharma peer statistics
        pf.np_peer_hcp_count,
        pf.np_peer_avg_spend_2022,
        pf.np_peer_p90_spend_2022,
        pf.np_peer_avg_spend_2023,
        pf.np_peer_p90_spend_2023,
        pf.np_peer_avg_spend_2024,
        pf.np_peer_p90_spend_2024,
        -- 3-year aggregate stats (pattern context only)
        pf.np_peer_avg_spend_3yr,
        pf.np_peer_median_spend_3yr,
        pf.np_peer_p90_spend_3yr,
        pf.np_peer_p95_spend_3yr,
        pf.np_peer_avg_risk_score,
        pf.np_peer_p90_risk_score,

        -- Peer group size and national fallback
        pf.np_peer_group_size,
        pf.np_use_national_benchmark,

        -- Spend vs peer avg ratios (capped at 10.0)
        pf.np_spend_vs_peer_avg_2022,
        pf.np_spend_vs_peer_avg_2023,
        pf.np_spend_vs_peer_avg_2024,
        pf.np_spend_vs_peer_avg_3yr,

        -- Per-year outlier flags
        pf.np_spend_outlier_2022,
        pf.np_spend_outlier_2023,
        pf.np_spend_outlier_2024,
        pf.np_outlier_years_count,
        CASE WHEN pf.np_outlier_years_count >= 2
             THEN true ELSE false END                                              AS np_persistent_outlier,

        -- Outlier flags based on specialty peer group (2024 primary)
        -- Used by Isolation Forest as binary features
        -- and by scorer.py for severity tier assignment
        CASE WHEN pf.np_spend_pct_rank_specialty_2024 > 0.90
             THEN true ELSE false END                                              AS np_spend_outlier,
        CASE WHEN pf.np_risk_pct_rank_specialty_2024  > 0.90
             THEN true ELSE false END                                              AS np_risk_outlier,
        CASE WHEN pf.np_risk_pct_rank_specialty_2024  > 0.99
             THEN true ELSE false END                                              AS np_top_1pct_risk,
        CASE WHEN pf.np_risk_pct_rank_specialty_2024  > 0.95
             THEN true ELSE false END                                              AS np_top_5pct_risk,
        CASE WHEN pf.np_risk_pct_rank_specialty_2024  > 0.90
             THEN true ELSE false END                                              AS np_top_10pct_risk,

        -- ─────────────────────────────────────────────────────────────────────
        -- TIER 2 — INDUSTRY-WIDE BENCHMARKS (ind_ / comp_ / sow_ prefix)
        -- ─────────────────────────────────────────────────────────────────────

        -- Industry HCP-level spend
        pf.industry_spend_2022,
        pf.industry_spend_2023,
        pf.industry_spend_2024,
        -- 3-year aggregate (pattern context only)
        pf.industry_spend_3yr,
        pf.industry_company_count_2024,
        pf.industry_payment_count_2024,

        -- Industry specialty benchmarks (per year)
        pf.ind_peer_hcp_count,
        pf.ind_peer_avg_spend_2022,
        pf.ind_peer_p90_spend_2022,
        pf.ind_peer_avg_spend_2023,
        pf.ind_peer_p90_spend_2023,
        pf.ind_peer_avg_spend_2024,
        pf.ind_peer_p90_spend_2024,
        pf.ind_peer_p95_spend_2024,

        -- Nova Pharma vs industry ratios (capped at 10.0)
        pf.np_vs_industry_ratio_2022,
        pf.np_vs_industry_ratio_2023,
        pf.np_vs_industry_ratio_2024,
        -- 3-year aggregate (pattern context only)
        pf.np_vs_industry_ratio_3yr,

        -- Industry outlier flags (per year and pattern)
        pf.ind_outlier_2022,
        pf.ind_outlier_2023,
        pf.ind_outlier_2024,
        pf.ind_high_outlier_2024,
        pf.ind_outlier_years_count,
        CASE WHEN pf.ind_outlier_years_count >= 2
             THEN true ELSE false END                                              AS ind_persistent_outlier,

        -- Share of wallet (annual — OIG red flag metric)
        pf.sow_2022,
        pf.sow_2023,
        pf.sow_2024,
        -- 3-year aggregate (pattern context only)
        pf.sow_3yr,
        pf.sow_dominant_2022,
        pf.sow_dominant_2023,
        pf.sow_dominant_2024,
        pf.sow_dominant_years_count,
        -- 2024 primary SOW flags
        CASE WHEN pf.sow_2024 > 0.80
             THEN true ELSE false END                                              AS sow_exclusive,
        CASE WHEN pf.sow_2024 > pf.sow_2023
              AND pf.sow_2023 > pf.sow_2022
             THEN true ELSE false END                                              AS sow_increasing,

        -- Competitor benchmarks (2024 primary)
        pf.competitor_spend_2022,
        pf.competitor_spend_2023,
        pf.competitor_spend_2024,
        -- 3-year aggregate (pattern context only)
        pf.competitor_spend_3yr,
        pf.competitor_company_count,
        pf.comp_peer_avg_spend_2024,
        pf.comp_peer_p90_spend_2024,
        pf.np_vs_competitor_ratio_2024,
        CASE WHEN pf.np_vs_competitor_ratio_2024 > 2.0
             THEN true ELSE false END                                              AS comp_spend_outlier,

        -- ─────────────────────────────────────────────────────────────────────
        -- ENGAGEMENT DECISION QUADRANT
        -- Primary signal: 2024. Escalation pattern uses all 3 years.
        -- ─────────────────────────────────────────────────────────────────────

        COALESCE(
            CASE
                -- 2024 high vs both benchmarks → investigate
                WHEN pf.np_spend_pct_rank_specialty_2024 > 0.75
                 AND pf.np_vs_industry_ratio_2024 > 1.5
                THEN 'investigate'

                -- Escalating rank pattern AND chronic near-cap → investigate
                WHEN (pf.np_yoy_rank_change_2223 > 0
                      AND pf.np_yoy_rank_change_2324 > 0)
                 AND pf.years_near_cap >= 2
                THEN 'investigate'

                -- High vs Nova Pharma norms, normal vs industry → review
                WHEN pf.np_spend_pct_rank_specialty_2024 > 0.75
                 AND pf.np_vs_industry_ratio_2024 <= 1.5
                THEN 'review'

                -- Normal vs Nova Pharma, but industry pays more → competitive_intelligence
                WHEN pf.np_spend_pct_rank_specialty_2024 <= 0.75
                 AND pf.np_vs_industry_ratio_2024 > 1.5
                THEN 'competitive_intelligence'

                ELSE 'continue'
            END,
            'continue'
        )                                                                          AS engagement_quadrant,

        -- Plain English reason combining key 2024 signals
        CONCAT(
            '2024 NP rank: ',
            CAST(CAST(ROUND(pf.np_spend_pct_rank_specialty_2024 * 100, 0) AS INTEGER) AS VARCHAR),
            'th pct. ',
            CAST(ROUND(pf.np_vs_industry_ratio_2024, 1) AS VARCHAR),
            'x industry avg (2024). NP outlier ',
            CAST(pf.np_outlier_years_count AS VARCHAR),
            '/3 yrs. SOW ',
            CAST(CAST(ROUND(pf.sow_2024 * 100, 0) AS INTEGER) AS VARCHAR),
            '% (2024).'
        )                                                                          AS engagement_quadrant_reason,

        -- Engagement priority score (0-100)
        -- Components: NP 2024 rank (30) + industry ratio (25) + SOW (25) + persistence (20)
        LEAST(100.0,
            -- Nova Pharma 2024 rank component (30 pts)
            LEAST(30.0, pf.np_spend_pct_rank_specialty_2024 * 30.0)
            -- Industry ratio component (25 pts; ratio/3.0 → full pts at 3× industry avg)
            + LEAST(25.0,
                LEAST(1.0, pf.np_vs_industry_ratio_2024 / 3.0) * 25.0)
            -- Share of wallet 2024 component (25 pts)
            + LEAST(25.0, pf.sow_2024 * 25.0)
            -- Persistence: NP outlier years (10 pts) + industry outlier years (10 pts)
            + LEAST(10.0, CAST(pf.np_outlier_years_count  AS DOUBLE) * 5.0)
            + LEAST(10.0, CAST(pf.ind_outlier_years_count AS DOUBLE) * 5.0)
        )                                                                          AS engagement_priority_score,

        -- ─────────────────────────────────────────────────────────────────────
        -- COMBINED FLAGS (highest-priority compound risk signals)
        -- ─────────────────────────────────────────────────────────────────────

        -- dual_outlier_flag: high vs BOTH benchmarks in 2024 — top priority
        CASE WHEN pf.np_spend_outlier_2024 = true
              AND pf.ind_outlier_2024 = true
             THEN true ELSE false END                                              AS dual_outlier_flag,

        -- triple_signal_flag: NP outlier + industry outlier + exclusive SOW in 2024
        -- Strongest possible compliance signal
        CASE WHEN pf.np_spend_outlier_2024 = true
              AND pf.ind_outlier_2024 = true
              AND pf.sow_2024 > 0.80
             THEN true ELSE false END                                              AS triple_signal_flag,

        -- escalating_risk_flag: rank AND SOW both growing year over year
        CASE WHEN (pf.np_yoy_rank_change_2223 > 0
                   AND pf.np_yoy_rank_change_2324 > 0)
              AND (pf.sow_2024 > pf.sow_2023
                   AND pf.sow_2023 > pf.sow_2022)
             THEN true ELSE false END                                              AS escalating_risk_flag,

        -- chronic_risk_flag: outlier vs both benchmarks in 2+ years (not one-off)
        CASE WHEN pf.np_outlier_years_count  >= 2
              AND pf.ind_outlier_years_count >= 2
             THEN true ELSE false END                                              AS chronic_risk_flag,

        -- ── Metadata ──────────────────────────────────────────────────────────
        CAST(CURRENT_TIMESTAMP AS timestamp)                                        AS mart_created_at

    FROM pre_final pf
)

SELECT * FROM final
