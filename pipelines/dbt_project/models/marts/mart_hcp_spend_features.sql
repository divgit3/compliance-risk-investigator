{{ config(materialized='table', tags=['phase2', 'features', 'ml_ready']) }}

-- ─────────────────────────────────────────────────────────────────────────────
-- mart_hcp_spend_features
-- Phase 2 — ML-ready external spend feature mart (one row per HCP)
--
-- Aggregates CMS Open Payments (Nova Pharma / Takeda) signals for anomaly
-- detection. HCPs with no CMS payments appear with 0-filled features and
-- has_cms_payments = false.
--
-- Violation flags are intentionally excluded (no label leakage into ML input).
--
-- Target compatibility:
--   athena — primary target; mart_target_payments lives in Athena / Glue.
--             stg_synthetic_interactions is not registered in Glue, so:
--               • hcp_master is derived from mart_target_payments (CMS HCPs only)
--               • rep concentration features are 0-filled (no rep_id in CMS)
--             Full 97K spine requires synthetic data registered in Glue (future).
--   duckdb  — will fail: mart_target_payments does not exist in DuckDB.
--             Run only with --target athena.
--
-- Business rules applied
--   Meal per-person limits (PhRMA Code 2022, Section 3):
--     breakfast $30 | lunch $75 | dinner $125
--     CMS Food & Beverage records carry no meal-type; $125 (dinner ceiling)
--     is applied as the single-record cap — the most common pharma meal format
--     and the most defensible single-transaction limit.
--   Annual compensation cap: $75,000 per HCP per year (OIG CPG / internal policy)
--   Rep concentration: sourced from synthetic interactions on DuckDB only;
--     0-filled on Athena until synthetic data is registered in Glue.
-- ─────────────────────────────────────────────────────────────────────────────

{% if target.type == 'athena' %}

WITH hcp_master AS (
    -- On Athena: spine = HCPs with at least one CMS payment from Nova Pharma.
    -- Full 97K spine is unavailable until synthetic parquet is registered in Glue.
    SELECT DISTINCT hcp_id
    FROM {{ ref('mart_target_payments') }}
    WHERE hcp_id IS NOT NULL
),

{% else %}

WITH hcp_master AS (
    -- On DuckDB: full 97K HCP spine from synthetic interactions
    SELECT DISTINCT hcp_id
    FROM {{ ref('stg_synthetic_interactions') }}
),

{% endif %}

cms_payments AS (
    -- Nova Pharma CMS payments with payment-category classification
    SELECT
        hcp_id,
        program_year,
        payment_amount,
        nature_of_payment,

        -- Payment category flags
        CASE
            WHEN nature_of_payment = 'Food and Beverage'
            THEN true ELSE false
        END                                                         AS is_meal,

        CASE
            WHEN LOWER(nature_of_payment) LIKE '%speaker%'
              OR LOWER(nature_of_payment) LIKE '%faculty%'
            THEN true ELSE false
        END                                                         AS is_speaking_fee,

        CASE
            WHEN nature_of_payment = 'Consulting Fee'
            THEN true ELSE false
        END                                                         AS is_consulting,

        -- Meal limit: dinner ceiling $125 applied to all F&B records
        CASE
            WHEN nature_of_payment = 'Food and Beverage'
             AND payment_amount > 125.0
            THEN true ELSE false
        END                                                         AS meal_over_limit,

        -- Overage as proportion of the $125 cap (NULL when not over limit)
        CASE
            WHEN nature_of_payment = 'Food and Beverage'
             AND payment_amount > 125.0
            THEN (payment_amount - 125.0) / 125.0
            ELSE NULL
        END                                                         AS meal_overage_pct

    FROM {{ ref('mart_target_payments') }}
    WHERE hcp_id IS NOT NULL
      AND payment_amount IS NOT NULL
),

hcp_year_agg AS (
    -- Per-HCP per-year aggregations from CMS payments
    SELECT
        hcp_id,
        program_year,
        SUM(payment_amount)                                           AS year_spend,
        COUNT(*)                                                      AS year_payment_count,
        SUM(CASE WHEN is_meal         THEN payment_amount ELSE 0 END) AS year_food_beverage,
        SUM(CASE WHEN is_speaking_fee THEN payment_amount ELSE 0 END) AS year_speaking_fee,
        SUM(CASE WHEN is_consulting   THEN payment_amount ELSE 0 END) AS year_consulting,
        COUNT(CASE WHEN is_speaking_fee THEN 1 END)                   AS year_speaking_count,
        COUNT(CASE WHEN is_meal       THEN 1 END)                     AS year_meal_count,
        COUNT(CASE WHEN meal_over_limit THEN 1 END)                   AS year_meals_over_limit,
        MAX(meal_overage_pct)                                         AS year_max_meal_overage_pct
    FROM cms_payments
    GROUP BY hcp_id, program_year
),

hcp_cross_year AS (
    -- Pivot year-level rows to one row per HCP
    SELECT
        hcp_id,

        -- Lifetime totals
        SUM(year_spend)                                               AS lifetime_total_spend,
        SUM(year_payment_count)                                       AS lifetime_payment_count,
        SUM(year_food_beverage)                                       AS food_beverage_total,
        SUM(year_speaking_fee)                                        AS speaking_fee_total,
        SUM(year_consulting)                                          AS consulting_fee_total,
        SUM(year_speaking_count)                                      AS speaking_fee_count,

        -- Per-year spend (0 when no payments in that year)
        MAX(CASE WHEN program_year = 2022 THEN year_spend ELSE 0 END) AS spend_2022,
        MAX(CASE WHEN program_year = 2023 THEN year_spend ELSE 0 END) AS spend_2023,
        MAX(CASE WHEN program_year = 2024 THEN year_spend ELSE 0 END) AS spend_2024,

        -- Active years: count of years with at least one payment
        COUNT(DISTINCT program_year)                                  AS active_payment_years,

        -- Meal breach totals
        SUM(year_meals_over_limit)                                    AS meals_over_limit_count,
        SUM(year_meal_count)                                          AS total_meal_count,
        MAX(year_max_meal_overage_pct)                                AS max_meal_overage_pct

    FROM hcp_year_agg
    GROUP BY hcp_id
),

{% if target.type != 'athena' %}

rep_agg AS (
    -- Rep concentration signals from synthetic interactions
    -- (CMS carries no rep_id; only available on DuckDB target)
    SELECT
        hcp_id,
        AVG(unique_reps_per_year)  AS avg_unique_reps,
        MAX(unique_reps_per_year)  AS max_unique_reps,
        MAX(top_rep_share)         AS top_rep_concentration_pct
    FROM (
        SELECT
            hcp_id,
            program_year,
            COUNT(DISTINCT rep_id)                                    AS unique_reps_per_year,
            CAST(MAX(rep_count) AS DOUBLE)
                / NULLIF(SUM(rep_count), 0)                           AS top_rep_share
        FROM (
            SELECT
                hcp_id,
                program_year,
                rep_id,
                COUNT(*)                                              AS rep_count
            FROM {{ ref('stg_synthetic_interactions') }}
            WHERE rep_id IS NOT NULL
            GROUP BY hcp_id, program_year, rep_id
        ) rep_counts
        GROUP BY hcp_id, program_year
    ) rep_by_year
    GROUP BY hcp_id
),

{% else %}

rep_agg AS (
    -- Rep concentration not available on Athena (synthetic data not in Glue).
    -- All fields 0-filled; populated when synthetic parquet is registered.
    SELECT
        hcp_id,
        CAST(0.0 AS DOUBLE)  AS avg_unique_reps,
        CAST(0   AS BIGINT)  AS max_unique_reps,
        CAST(0.0 AS DOUBLE)  AS top_rep_concentration_pct
    FROM hcp_master
),

{% endif %}

hcp_features AS (
    -- Compute ratios, flags, and raw risk score for HCPs with CMS payments
    SELECT
        cy.hcp_id,

        -- ── Volume ────────────────────────────────────────────────────────────
        cy.lifetime_total_spend,
        cy.lifetime_payment_count,
        cy.spend_2022,
        cy.spend_2023,
        cy.spend_2024,
        GREATEST(cy.spend_2022, cy.spend_2023, cy.spend_2024)         AS peak_year_spend,
        cy.active_payment_years,

        -- ── Annual cap proximity ($75K/yr per OIG CPG) ─────────────────────
        GREATEST(cy.spend_2022, cy.spend_2023, cy.spend_2024)
            / 75000.0                                                  AS annual_cap_pct_used,
        CASE WHEN GREATEST(cy.spend_2022, cy.spend_2023, cy.spend_2024)
                  >= 75000.0 THEN true ELSE false END                  AS at_cap_flag,
        CASE WHEN GREATEST(cy.spend_2022, cy.spend_2023, cy.spend_2024)
                  >= 60000.0 THEN true ELSE false END                  AS near_cap_flag,

        -- ── Meal limits ($125 dinner ceiling on F&B records) ──────────────
        cy.meals_over_limit_count,
        CASE WHEN cy.total_meal_count > 0
             THEN CAST(cy.meals_over_limit_count AS DOUBLE)
                  / cy.total_meal_count
             ELSE 0.0
        END                                                            AS meal_breach_rate,
        COALESCE(cy.max_meal_overage_pct, 0.0)                         AS max_meal_overage_pct,

        -- ── YoY trend (NULL = no base-year spend, not a zero) ─────────────
        CASE WHEN cy.spend_2022 > 0
             THEN (cy.spend_2023 - cy.spend_2022) / cy.spend_2022
             ELSE NULL
        END                                                            AS yoy_growth_2223,
        CASE WHEN cy.spend_2023 > 0
             THEN (cy.spend_2024 - cy.spend_2023) / cy.spend_2023
             ELSE NULL
        END                                                            AS yoy_growth_2324,
        -- True only when spend grew in both consecutive year-pairs
        CASE WHEN cy.spend_2022 > 0
              AND cy.spend_2023 > cy.spend_2022
              AND cy.spend_2024 > cy.spend_2023
             THEN true ELSE false
        END                                                            AS multi_year_increasing_flag,

        -- ── Payment mix (shares of lifetime spend) ────────────────────────
        CASE WHEN cy.lifetime_total_spend > 0
             THEN cy.food_beverage_total  / cy.lifetime_total_spend
             ELSE 0.0 END                                              AS pct_food_beverage,
        CASE WHEN cy.lifetime_total_spend > 0
             THEN cy.speaking_fee_total   / cy.lifetime_total_spend
             ELSE 0.0 END                                              AS pct_speaking_fee,
        CASE WHEN cy.lifetime_total_spend > 0
             THEN cy.consulting_fee_total / cy.lifetime_total_spend
             ELSE 0.0 END                                              AS pct_consulting,
        cy.speaking_fee_total,
        cy.speaking_fee_count,
        cy.consulting_fee_total,
        cy.food_beverage_total,

        -- ── Rep concentration (synthetic data — 0 on Athena) ─────────────
        COALESCE(r.avg_unique_reps,           0.0) AS avg_unique_reps,
        COALESCE(r.max_unique_reps,           0)   AS max_unique_reps,
        COALESCE(r.top_rep_concentration_pct, 0.0) AS top_rep_concentration_pct

    FROM hcp_cross_year cy
    LEFT JOIN rep_agg r ON cy.hcp_id = r.hcp_id
),

final AS (
    -- LEFT JOIN HCP spine to features; COALESCE nulls → 0 for ML compatibility.
    -- HCPs with no CMS payments receive 0s and has_cms_payments = false.
    SELECT
        m.hcp_id,

        -- ── Volume ────────────────────────────────────────────────────────────
        COALESCE(f.lifetime_total_spend,   0.0) AS lifetime_total_spend,
        COALESCE(f.lifetime_payment_count, 0)   AS lifetime_payment_count,
        COALESCE(f.spend_2022,             0.0) AS spend_2022,
        COALESCE(f.spend_2023,             0.0) AS spend_2023,
        COALESCE(f.spend_2024,             0.0) AS spend_2024,
        COALESCE(f.peak_year_spend,        0.0) AS peak_year_spend,
        COALESCE(f.active_payment_years,   0)   AS active_payment_years,

        -- ── Annual cap ────────────────────────────────────────────────────────
        COALESCE(f.annual_cap_pct_used,    0.0)   AS annual_cap_pct_used,
        COALESCE(f.at_cap_flag,            false)  AS at_cap_flag,
        COALESCE(f.near_cap_flag,          false)  AS near_cap_flag,

        -- ── Meal limits ───────────────────────────────────────────────────────
        COALESCE(f.meals_over_limit_count, 0)   AS meals_over_limit_count,
        COALESCE(f.meal_breach_rate,       0.0) AS meal_breach_rate,
        COALESCE(f.max_meal_overage_pct,   0.0) AS max_meal_overage_pct,

        -- ── YoY trend (intentionally nullable — NULL ≠ zero growth) ──────────
        f.yoy_growth_2223,
        f.yoy_growth_2324,
        COALESCE(f.multi_year_increasing_flag, false) AS multi_year_increasing_flag,

        -- ── Payment mix ───────────────────────────────────────────────────────
        COALESCE(f.pct_food_beverage,      0.0) AS pct_food_beverage,
        COALESCE(f.pct_speaking_fee,       0.0) AS pct_speaking_fee,
        COALESCE(f.pct_consulting,         0.0) AS pct_consulting,
        COALESCE(f.speaking_fee_total,     0.0) AS speaking_fee_total,
        COALESCE(f.speaking_fee_count,     0)   AS speaking_fee_count,
        COALESCE(f.consulting_fee_total,   0.0) AS consulting_fee_total,
        COALESCE(f.food_beverage_total,    0.0) AS food_beverage_total,

        -- ── Rep concentration ─────────────────────────────────────────────────
        COALESCE(f.avg_unique_reps,            0.0) AS avg_unique_reps,
        COALESCE(f.max_unique_reps,            0)   AS max_unique_reps,
        COALESCE(f.top_rep_concentration_pct,  0.0) AS top_rep_concentration_pct,

        -- ── Composite heuristic risk score (0-100, pre-ML) ───────────────────
        -- Weights: cap proximity 30 | meal breaches 25 | overage magnitude 20
        --          speaking fee mix 15 | multi-year escalation 10
        LEAST(100.0,
            LEAST(30.0, COALESCE(f.annual_cap_pct_used,    0.0) * 30.0)
            + LEAST(25.0, COALESCE(f.meal_breach_rate,     0.0) * 250.0)
            + LEAST(20.0, COALESCE(f.max_meal_overage_pct, 0.0) * 20.0)
            + LEAST(15.0, COALESCE(f.pct_speaking_fee,     0.0) * 15.0)
            + CASE WHEN COALESCE(f.multi_year_increasing_flag, false)
                   THEN 10.0 ELSE 0.0 END
        )                                           AS raw_spend_risk_score,

        -- ── Metadata ──────────────────────────────────────────────────────────
        CASE WHEN f.hcp_id IS NOT NULL THEN true ELSE false END AS has_cms_payments,
        CAST(NOW() AS TIMESTAMP)                                 AS mart_created_at

    FROM hcp_master m
    LEFT JOIN hcp_features f ON m.hcp_id = f.hcp_id
)

SELECT * FROM final
