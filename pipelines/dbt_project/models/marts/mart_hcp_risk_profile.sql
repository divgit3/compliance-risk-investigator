{{ config(materialized='table', tags=['phase2', 'features', 'ml_ready', 'risk_spine']) }}

-- ─────────────────────────────────────────────────────────────────────────────
-- mart_hcp_risk_profile
-- Phase 2 — Master HCP risk spine (one row per HCP)
--
-- Joins all Phase 2 feature marts into a single ML-ready table that is the
-- primary input for the Isolation Forest, rule-based flags, and the unified
-- scorer (Task 2.10).
--
-- Source contributions:
--   mart_hcp_spend_features       → CMS external payment signals (what Nova
--                                   Pharma reported to the government)
--   mart_event_features           → Speaker event signals aggregated to HCP
--                                   level as speaker (OIG Fraud Alert signals)
--   mart_hcp_interactions_features → Internal CRM signals (meal frequency,
--                                   rep visits, FMV compliance, documentation)
--   mart_violation_ground_truth   → VALIDATION ONLY — violation labels for
--                                   post-hoc model evaluation. Never ML input.
--
-- Cross-engine constraints:
--   athena — mart_hcp_spend_features exists; event/interaction/GT do not.
--            Spine = mart_hcp_spend_features. Event + interaction features
--            are 0-filled until synthetic data is registered in Glue.
--   duckdb — mart_event_features, interactions, and GT exist; spend features
--            do not. Spine = stg_synthetic_interactions. Spend features
--            are 0-filled.
--
-- Business rules sourced from compliance/rules.json:
--   COMP_001: annual cap $75,000
--   MEAL_003: dinner ceiling $100 (Nova Pharma override)
--   SPEAKER_001: speaker FMV ceiling $3,500 (Nova Pharma override)
--   COMP_003: near-cap threshold 80%
--   ATTEST_001: min attestation rate 80%
-- ─────────────────────────────────────────────────────────────────────────────

-- ── Step 1: HCP spine (target-conditional) ───────────────────────────────────

{% if target.type == 'athena' %}

WITH hcp_spine AS (
    -- Athena: spine from mart_hcp_spend_features (97,011 CMS-known HCPs)
    SELECT
        hcp_id,
        CAST(NULL AS VARCHAR) AS practice_city,
        CAST(NULL AS VARCHAR) AS state,
        CAST(NULL AS VARCHAR) AS specialty,
        false                 AS is_kol
    FROM {{ ref('mart_hcp_spend_features') }}
),

{% else %}

WITH hcp_spine AS (
    -- DuckDB: full 97K HCP spine from synthetic interaction records
    -- Aggregates to one row per HCP, taking the most frequent city/state/specialty
    SELECT
        hcp_id,
        -- Use MODE (most frequent value) approximated via ARG_MAX on count
        -- practice_city: taken from the most common practice_id seen for this HCP
        MAX(practice_city)     AS practice_city,
        MAX(interaction_state) AS state,
        CAST(NULL AS VARCHAR)  AS specialty,    -- not in interaction records
        false                  AS is_kol        -- not in interaction records
    FROM {{ ref('mart_hcp_interactions_features') }}
    GROUP BY hcp_id
),

{% endif %}

-- ── Step 2: CMS spend signals (target-conditional) ───────────────────────────

{% if target.type == 'athena' %}

spend_features AS (
    SELECT * FROM {{ ref('mart_hcp_spend_features') }}
),

{% else %}

spend_features AS (
    -- DuckDB: CMS spend features unavailable — 0-filled
    -- Populated on Athena target only
    SELECT
        hcp_id,
        CAST(0.0  AS DOUBLE)  AS lifetime_total_spend,
        CAST(0    AS BIGINT)  AS lifetime_payment_count,
        CAST(0.0  AS DOUBLE)  AS spend_2022,
        CAST(0.0  AS DOUBLE)  AS spend_2023,
        CAST(0.0  AS DOUBLE)  AS spend_2024,
        CAST(0.0  AS DOUBLE)  AS peak_year_spend,
        CAST(0    AS BIGINT)  AS active_payment_years,
        CAST(0.0  AS DOUBLE)  AS annual_cap_pct_used,
        false                 AS at_cap_flag,
        false                 AS near_cap_flag,
        CAST(0    AS BIGINT)  AS meals_over_limit_count,
        CAST(0.0  AS DOUBLE)  AS meal_breach_rate,
        CAST(0.0  AS DOUBLE)  AS max_meal_overage_pct,
        CAST(NULL AS DOUBLE)  AS yoy_growth_2223,
        CAST(NULL AS DOUBLE)  AS yoy_growth_2324,
        false                 AS multi_year_increasing_flag,
        CAST(0.0  AS DOUBLE)  AS pct_food_beverage,
        CAST(0.0  AS DOUBLE)  AS pct_speaking_fee,
        CAST(0.0  AS DOUBLE)  AS pct_consulting,
        CAST(0.0  AS DOUBLE)  AS speaking_fee_total,
        CAST(0    AS BIGINT)  AS speaking_fee_count,
        CAST(0.0  AS DOUBLE)  AS consulting_fee_total,
        CAST(0.0  AS DOUBLE)  AS food_beverage_total,
        CAST(0.0  AS DOUBLE)  AS avg_unique_reps,
        CAST(0    AS BIGINT)  AS max_unique_reps,
        CAST(0.0  AS DOUBLE)  AS top_rep_concentration_pct,
        CAST(0.0  AS DOUBLE)  AS raw_spend_risk_score,
        false                 AS has_cms_payments
    FROM hcp_spine
),

{% endif %}

-- ── Step 3: Event signals aggregated to HCP level (DuckDB only) ──────────────

{% if target.type == 'athena' %}

event_agg AS (
    -- Athena: speaker event features unavailable — 0-filled
    -- Populated on DuckDB target only (synthetic data not in Glue)
    SELECT
        hcp_id,
        CAST(0    AS BIGINT) AS total_events_as_speaker,
        CAST(0.0  AS DOUBLE) AS avg_event_risk_score,
        CAST(0.0  AS DOUBLE) AS max_event_risk_score,
        CAST(0    AS BIGINT) AS events_with_low_attendance,
        CAST(0    AS BIGINT) AS events_over_fmv,
        CAST(0    AS BIGINT) AS events_missing_attestation,
        CAST(0    AS BIGINT) AS events_rapid_repeat,
        CAST(0.0  AS DOUBLE) AS total_speaker_fees_events,
        CAST(0.0  AS DOUBLE) AS pct_events_over_fmv
    FROM hcp_spine
),

{% else %}

event_agg AS (
    -- Aggregate mart_event_features (event level) to HCP level as speaker
    -- SPEAKER_001: FMV ceiling $3,500 (rules.json)
    -- SPEAKER_004: low attendance < 3 attendees (rules.json)
    -- ATTEST_001: missing attestation < 80% signed (rules.json)
    -- SPEAKER_005: rapid repeat < 30 days (rules.json)
    SELECT
        speaker_hcp_id                                          AS hcp_id,
        COUNT(*)                                                AS total_events_as_speaker,
        AVG(raw_event_risk_score)                               AS avg_event_risk_score,
        MAX(raw_event_risk_score)                               AS max_event_risk_score,
        COUNT(CASE WHEN low_attendance_flag   THEN 1 END)       AS events_with_low_attendance,
        -- SPEAKER_001: events over $3,500 FMV ceiling (rules.json)
        COUNT(CASE WHEN speaker_fee_over_fmv_flag THEN 1 END)   AS events_over_fmv,
        -- ATTEST_001: events where < 80% of attendees signed (rules.json)
        COUNT(CASE WHEN missing_attestation_flag THEN 1 END)    AS events_missing_attestation,
        -- SPEAKER_005: events following another within 30 days (rules.json)
        COUNT(CASE WHEN rapid_repeat_flag     THEN 1 END)       AS events_rapid_repeat,
        SUM(speaker_fee)                                        AS total_speaker_fees_events,
        CASE WHEN COUNT(*) > 0
             THEN CAST(COUNT(CASE WHEN speaker_fee_over_fmv_flag THEN 1 END) AS DOUBLE)
                  / COUNT(*)
             ELSE 0.0
        END                                                     AS pct_events_over_fmv
    FROM {{ ref('mart_event_features') }}
    GROUP BY speaker_hcp_id
),

{% endif %}

-- ── Step 4: Interaction signals aggregated to HCP level (DuckDB only) ─────────

{% if target.type == 'athena' %}

interaction_features AS (
    -- Athena: interaction features unavailable — 0-filled
    -- Populated on DuckDB target only (synthetic data not in Glue)
    SELECT
        hcp_id,
        CAST(0   AS BIGINT) AS total_interactions,
        CAST(0   AS BIGINT) AS total_meals,
        CAST(0.0 AS DOUBLE) AS avg_meal_cost,
        CAST(0   AS BIGINT) AS interactions_with_vague_rationale,
        CAST(0.0 AS DOUBLE) AS fmv_compliance_rate,
        CAST(0   AS BIGINT) AS unique_reps_interacted,
        CAST(0.0 AS DOUBLE) AS interaction_frequency_score
    FROM hcp_spine
),

{% else %}

interaction_features AS (
    -- Aggregate mart_hcp_interactions_features (interaction level) to HCP level
    -- "Vague rationale" = empty string, 'Meeting', or 'Other' (single-word responses)
    -- fmv_compliance_rate = fraction of interactions where FMV was not exceeded
    -- MEAL_003: meal cost ceiling $100 Nova Pharma dinner (rules.json)
    SELECT
        hcp_id,
        COUNT(*)                                                AS total_interactions,
        COUNT(CASE WHEN interaction_type = 'meal' THEN 1 END)   AS total_meals,
        AVG(CASE WHEN interaction_type = 'meal'
                 THEN meal_cost END)                            AS avg_meal_cost,
        -- Vague rationale: empty, 'Meeting', or 'Other' — indicates documentation risk
        COUNT(CASE WHEN business_rationale IN ('', 'Meeting', 'Other')
                       OR business_rationale IS NULL
                   THEN 1 END)                                  AS interactions_with_vague_rationale,
        -- FMV compliance rate: fraction of interactions where FMV was not exceeded
        CASE WHEN COUNT(*) > 0
             THEN CAST(COUNT(CASE WHEN fmv_approved = true THEN 1 END) AS DOUBLE)
                  / COUNT(*)
             ELSE 1.0
        END                                                     AS fmv_compliance_rate,
        COUNT(DISTINCT rep_id)                                  AS unique_reps_interacted,
        -- interaction_frequency_score: 0-100 heuristic
        --   Interaction volume:    up to 50 pts (100 interactions = 50 pts)
        --   FMV violations:        up to 30 pts (0% FMV compliance = 30 pts)
        --   Documentation quality: up to 20 pts (100% vague = 20 pts)
        LEAST(100.0,
            LEAST(50.0, (CAST(COUNT(*) AS DOUBLE) / 100.0) * 50.0)
            + LEAST(30.0,
                (1.0 - CASE WHEN COUNT(*) > 0
                             THEN CAST(COUNT(CASE WHEN fmv_approved = true THEN 1 END) AS DOUBLE)
                                  / COUNT(*)
                             ELSE 1.0 END
                ) * 30.0
            )
            + LEAST(20.0,
                (CAST(COUNT(CASE WHEN business_rationale IN ('', 'Meeting', 'Other')
                                     OR business_rationale IS NULL
                                 THEN 1 END) AS DOUBLE)
                 / NULLIF(COUNT(*), 0)
                ) * 20.0
            )
        )                                                       AS interaction_frequency_score
    FROM {{ ref('mart_hcp_interactions_features') }}
    GROUP BY hcp_id
),

{% endif %}

-- ── Step 5: Ground truth aggregated to HCP level (DuckDB only) ────────────────

{% if target.type == 'athena' %}

ground_truth_agg AS (
    -- Athena: ground truth unavailable — 0-filled
    SELECT
        hcp_id,
        CAST(0         AS BIGINT)  AS ground_truth_violation_count,
        CAST('none'    AS VARCHAR) AS ground_truth_max_severity
    FROM hcp_spine
),

{% else %}

ground_truth_agg AS (
    -- Aggregate violation ground truth to HCP level
    -- VALIDATION ONLY: DO NOT USE AS ML FEATURES
    SELECT
        hcp_id,
        COUNT(CASE WHEN is_violation = true THEN 1 END)         AS ground_truth_violation_count,
        -- Severity ordinal: none < low < medium < high
        -- MAX over the string ordering happens to be alphabetically wrong;
        -- use CASE to convert to ordinal then back to label
        CASE MAX(
            CASE violation_severity
                WHEN 'none'   THEN 0
                WHEN 'low'    THEN 1
                WHEN 'medium' THEN 2
                WHEN 'high'   THEN 3
                ELSE 0
            END
        )
            WHEN 3 THEN 'high'
            WHEN 2 THEN 'medium'
            WHEN 1 THEN 'low'
            ELSE 'none'
        END                                                     AS ground_truth_max_severity
    FROM {{ ref('mart_violation_ground_truth') }}
    GROUP BY hcp_id
),

{% endif %}

-- ── Step 6: Final — join all CTEs, compute combined scores ───────────────────

final AS (
    SELECT
        -- ── Identity ──────────────────────────────────────────────────────────
        s.hcp_id,
        CAST(NULL AS VARCHAR)                       AS hcp_name,      -- not in synthetic data
        sp.practice_city                            AS city,
        sp.state,
        sp.specialty,
        sp.is_kol,
        CAST(NULL AS BOOLEAN)                       AS is_high_prescriber, -- future: from hcp_master

        -- ── CMS spend signals (from mart_hcp_spend_features) ──────────────────
        COALESCE(s.lifetime_total_spend,        0.0)    AS lifetime_total_spend,
        COALESCE(s.peak_year_spend,             0.0)    AS peak_year_spend,
        COALESCE(s.annual_cap_pct_used,         0.0)    AS annual_cap_pct_used,
        COALESCE(s.at_cap_flag,                 false)  AS at_cap_flag,
        COALESCE(s.near_cap_flag,               false)  AS near_cap_flag,
        COALESCE(s.meal_breach_rate,            0.0)    AS meal_breach_rate,
        COALESCE(s.max_meal_overage_pct,        0.0)    AS max_meal_overage_pct,
        COALESCE(s.pct_speaking_fee,            0.0)    AS pct_speaking_fee,
        COALESCE(s.multi_year_increasing_flag,  false)  AS multi_year_increasing_flag,
        COALESCE(s.raw_spend_risk_score,        0.0)    AS raw_spend_risk_score,

        -- ── Speaker event signals (aggregated from mart_event_features) ────────
        COALESCE(e.total_events_as_speaker,     0)      AS total_events_as_speaker,
        COALESCE(e.avg_event_risk_score,        0.0)    AS avg_event_risk_score,
        COALESCE(e.max_event_risk_score,        0.0)    AS max_event_risk_score,
        COALESCE(e.events_with_low_attendance,  0)      AS events_with_low_attendance,
        COALESCE(e.events_over_fmv,             0)      AS events_over_fmv,
        COALESCE(e.events_missing_attestation,  0)      AS events_missing_attestation,
        COALESCE(e.events_rapid_repeat,         0)      AS events_rapid_repeat,
        COALESCE(e.total_speaker_fees_events,   0.0)    AS total_speaker_fees_events,
        COALESCE(e.pct_events_over_fmv,         0.0)    AS pct_events_over_fmv,

        -- ── Interaction signals (aggregated from mart_hcp_interactions_features)
        COALESCE(i.total_interactions,              0)      AS total_interactions,
        COALESCE(i.total_meals,                     0)      AS total_meals,
        COALESCE(i.avg_meal_cost,                   0.0)    AS avg_meal_cost,
        COALESCE(i.interactions_with_vague_rationale, 0)    AS interactions_with_vague_rationale,
        COALESCE(i.fmv_compliance_rate,             1.0)    AS fmv_compliance_rate,
        COALESCE(i.unique_reps_interacted,          0)      AS unique_reps_interacted,
        COALESCE(i.interaction_frequency_score,     0.0)    AS interaction_frequency_score,

        -- ── Data presence flags ───────────────────────────────────────────────
        COALESCE(s.has_cms_payments, false)             AS has_cms_payments,
        CASE WHEN COALESCE(e.total_events_as_speaker, 0) > 0
             THEN true ELSE false END                   AS has_speaker_events,
        CASE WHEN COALESCE(i.total_interactions, 0) > 0
             THEN true ELSE false END                   AS has_interactions,
        -- 0-3 points: one per data source present
        (CASE WHEN COALESCE(s.has_cms_payments, false)                    THEN 1 ELSE 0 END
         + CASE WHEN COALESCE(e.total_events_as_speaker, 0) > 0           THEN 1 ELSE 0 END
         + CASE WHEN COALESCE(i.total_interactions, 0) > 0                THEN 1 ELSE 0 END
        )                                               AS data_completeness_score,

        -- ── Risk signal count ─────────────────────────────────────────────────
        -- Count of distinct risk signals that have fired for this HCP
        -- COMP_001/COMP_003: annual cap signals (rules.json)
        -- SPEAKER_001: events over FMV (rules.json)
        -- SPEAKER_004: low attendance (rules.json)
        -- ATTEST_001: missing attestation (rules.json)
        (CASE WHEN COALESCE(s.at_cap_flag,               false) THEN 1 ELSE 0 END
         + CASE WHEN COALESCE(s.near_cap_flag,            false) THEN 1 ELSE 0 END
         + CASE WHEN COALESCE(s.multi_year_increasing_flag,false) THEN 1 ELSE 0 END
         + CASE WHEN COALESCE(e.events_over_fmv,          0) > 0 THEN 1 ELSE 0 END
         + CASE WHEN COALESCE(e.events_with_low_attendance,0) > 0 THEN 1 ELSE 0 END
         + CASE WHEN COALESCE(e.events_missing_attestation,0) > 0 THEN 1 ELSE 0 END
        )                                               AS risk_signal_count,

        -- ── PRE-ML heuristic combined risk score (0-100) ─────────────────────
        -- Task 2.10 scorer.py replaces this with the unified ML-informed score.
        --
        -- Weights when all three sources present:
        --   CMS spend:    40%  (external, government-reported)
        --   Speaker events: 35% (OIG Fraud Alert primary signal)
        --   Interactions: 25%  (internal CRM)
        --
        -- Null-safe: reweight proportionally using only available sources.
        -- On Athena (no events/interactions): spend * 1.0
        -- On DuckDB (no spend):               events * 0.58 + interactions * 0.42
        --   (0.35 / 0.60 ≈ 0.58,  0.25 / 0.60 ≈ 0.42)
        CASE
            -- All three present
            WHEN COALESCE(s.has_cms_payments, false)
             AND COALESCE(e.total_events_as_speaker, 0) > 0
             AND COALESCE(i.total_interactions, 0) > 0
            THEN (COALESCE(s.raw_spend_risk_score,        0.0) * 0.40
                + COALESCE(e.avg_event_risk_score,         0.0) * 0.35
                + COALESCE(i.interaction_frequency_score,  0.0) * 0.25)

            -- Spend + events only (no interactions)
            WHEN COALESCE(s.has_cms_payments, false)
             AND COALESCE(e.total_events_as_speaker, 0) > 0
            THEN (COALESCE(s.raw_spend_risk_score,  0.0) * (0.40 / 0.75)
                + COALESCE(e.avg_event_risk_score,   0.0) * (0.35 / 0.75))

            -- Spend + interactions only (no events)
            WHEN COALESCE(s.has_cms_payments, false)
             AND COALESCE(i.total_interactions, 0) > 0
            THEN (COALESCE(s.raw_spend_risk_score,         0.0) * (0.40 / 0.65)
                + COALESCE(i.interaction_frequency_score,  0.0) * (0.25 / 0.65))

            -- Events + interactions only (no CMS — DuckDB typical case)
            WHEN COALESCE(e.total_events_as_speaker, 0) > 0
             AND COALESCE(i.total_interactions, 0) > 0
            THEN (COALESCE(e.avg_event_risk_score,         0.0) * (0.35 / 0.60)
                + COALESCE(i.interaction_frequency_score,  0.0) * (0.25 / 0.60))

            -- Spend only (Athena with no synthetic data in Glue)
            WHEN COALESCE(s.has_cms_payments, false)
            THEN COALESCE(s.raw_spend_risk_score, 0.0)

            -- Events only
            WHEN COALESCE(e.total_events_as_speaker, 0) > 0
            THEN COALESCE(e.avg_event_risk_score, 0.0)

            -- Interactions only
            WHEN COALESCE(i.total_interactions, 0) > 0
            THEN COALESCE(i.interaction_frequency_score, 0.0)

            ELSE 0.0
        END                                             AS combined_raw_risk_score,

        -- ── VALIDATION ONLY — DO NOT USE AS ML FEATURES ───────────────────────
        -- These fields join violation ground truth for post-hoc model evaluation.
        -- They must NEVER be included in the feature set passed to the Isolation
        -- Forest, rule_based_flags.py, or scorer.py. Doing so would constitute
        -- target leakage and invalidate all model performance metrics.
        COALESCE(g.ground_truth_violation_count,  0)        AS ground_truth_violation_count,
        COALESCE(g.ground_truth_max_severity, 'none')       AS ground_truth_max_severity,

        -- ── Metadata ──────────────────────────────────────────────────────────
        CURRENT_TIMESTAMP                                   AS mart_created_at

    FROM hcp_spine sp
    LEFT JOIN spend_features       s  ON sp.hcp_id = s.hcp_id
    LEFT JOIN event_agg            e  ON sp.hcp_id = e.hcp_id
    LEFT JOIN interaction_features i  ON sp.hcp_id = i.hcp_id
    LEFT JOIN ground_truth_agg     g  ON sp.hcp_id = g.hcp_id
)

SELECT * FROM final
