{{ config(materialized='table', tags=['phase2', 'features', 'ml_ready']) }}

-- ─────────────────────────────────────────────────────────────────────────────
-- mart_event_features
-- Phase 2 — ML-ready speaker event feature mart (one row per event)
--
-- Aggregates synthetic speaker program data into anomaly detection features.
-- Speaker programs are a priority OIG enforcement area (Fraud Alert 2020):
-- nominal educational events are commonly used to disguise HCP compensation.
--
-- Violation flags are intentionally excluded (no label leakage into ML input).
--
-- Business rules sourced from compliance/rules.json:
--   MEAL_003    per-head meal ceiling: $100 (Nova Pharma dinner — stricter than PhRMA $125)
--   SPEAKER_001 speaker fee FMV ceiling: $3,500 (Nova Pharma — stricter than $4,000)
--   SPEAKER_002 high repeat threshold: > 6 events/year
--   SPEAKER_003 repeat speaker threshold: > 3 events/year
--   SPEAKER_004 min attendees per event: 3
--   SPEAKER_005 rapid repeat window: 30 days
--   VENUE_001   max venue cost: $3,000
--   VENUE_002   max total program cost: $8,000
--   ATTEST_001  min attestation rate: 0.80 (80%)
--
-- Target: DuckDB only — source parquets accessed via httpfs S3 reader.
-- ─────────────────────────────────────────────────────────────────────────────

WITH events_base AS (
    -- Clean identity, cost, and compliance fields from speaker program records.
    -- Violation flags excluded. program_year cast to integer for window partitions.
    SELECT
        event_id,
        CAST(event_date AS DATE)                        AS event_date,
        CAST(program_year AS INTEGER)                   AS event_year,
        speaker_hcp_id,
        venue_city,
        venue_state,
        product_featured,
        compliance_approved,

        -- Cost columns
        COALESCE(speaker_fee,           0.0)            AS speaker_fee,
        COALESCE(venue_cost,            0.0)            AS venue_cost,
        COALESCE(travel_reimbursement,  0.0)            AS travel_reimbursement,
        COALESCE(total_program_cost,    0.0)            AS total_program_cost,

        -- Raw attendee count from events table (used when attendee table unavailable)
        COALESCE(attendee_count, 0)                     AS attendee_count_raw

    FROM {{ ref('stg_synthetic_speaker_programs') }}
    WHERE event_id IS NOT NULL
),

attendee_agg AS (
    -- Per-event attendee aggregations from attendee-level records.
    -- attendee_count: total attendees at event (hcp + non-hcp)
    -- signed_count: attendees who signed attestation forms
    -- attendees_signed_pct: fraction signed — used for ATTEST_001 check
    SELECT
        event_id,
        COUNT(*)                                                    AS attendee_count,
        COUNT(CASE WHEN signed_attestation = true THEN 1 END)       AS signed_count,
        CAST(
            COUNT(CASE WHEN signed_attestation = true THEN 1 END)
            AS DOUBLE
        ) / NULLIF(COUNT(*), 0)                                     AS attendees_signed_pct

    FROM {{ ref('stg_synthetic_attendees') }}
    GROUP BY event_id
),

speaker_window AS (
    -- Window functions for repeat-speaker and rapid-repeat detection.
    -- Partitioned by (speaker_hcp_id, event_year) so counts reset each year.
    -- Ordered by event_date ASC so LAG correctly references the prior event.
    SELECT
        event_id,
        speaker_hcp_id,
        event_date,
        event_year,

        -- Count of events this speaker has given in this calendar year
        -- (includes current event — evaluated as cumulative count up to this row)
        COUNT(*) OVER (
            PARTITION BY speaker_hcp_id, event_year
        )                                                           AS events_same_speaker_year,

        -- Days since this speaker's previous event (NULL for first event per year)
        -- SPEAKER_005: rapid repeat if < 30 days
        date_diff('day',
            LAG(CAST(event_date AS DATE)) OVER (
                PARTITION BY speaker_hcp_id, event_year
                ORDER BY event_date ASC
            ),
            CAST(event_date AS DATE)
        )                                                           AS days_since_last_event_same_speaker

    FROM events_base
),

cost_features AS (
    -- Derived cost ratios and flags joining events with attendee counts.
    -- Uses attendee_agg.attendee_count (from attendee records) as primary;
    -- falls back to events_base.attendee_count_raw if attendee record is missing.
    SELECT
        e.event_id,
        COALESCE(a.attendee_count, e.attendee_count_raw)            AS attendee_count,
        COALESCE(a.attendees_signed_pct, 0.0)                       AS attendees_signed_pct,

        -- meal_cost_per_attendee: total program cost / attendees
        -- Proxy for per-head spend; no separate meal column at event level
        -- MEAL_003 ceiling: $100 per head (Nova Pharma dinner limit)
        CASE
            WHEN COALESCE(a.attendee_count, e.attendee_count_raw) > 0
            THEN e.total_program_cost
                 / COALESCE(a.attendee_count, e.attendee_count_raw)
            ELSE 0.0
        END                                                         AS meal_cost_per_attendee,

        -- venue_cost_pct_of_total: venue as share of total program spend
        -- High share indicates potential venue inflation risk
        CASE
            WHEN e.total_program_cost > 0
            THEN e.venue_cost / e.total_program_cost
            ELSE 0.0
        END                                                         AS venue_cost_pct_of_total,

        -- speaker_fee_fmv_pct: speaker fee as fraction of FMV ceiling
        -- SPEAKER_001 ceiling: $3,500 (Nova Pharma override — rules.json)
        e.speaker_fee / 3500.0                                      AS speaker_fee_fmv_pct

    FROM events_base e
    LEFT JOIN attendee_agg a ON e.event_id = a.event_id
),

final AS (
    SELECT
        -- ── Identity ──────────────────────────────────────────────────────────
        e.event_id,
        e.event_date,
        e.event_year,
        e.speaker_hcp_id,
        e.venue_city,
        e.venue_state,
        e.product_featured,
        e.compliance_approved,

        -- ── Attendance (SPEAKER_004: low < 3, very_low < 2) ──────────────────
        COALESCE(cf.attendee_count, 0)                              AS attendee_count,
        -- SPEAKER_004: min attendees per event = 3 (rules.json)
        CASE WHEN COALESCE(cf.attendee_count, 0) < 3
             THEN true ELSE false END                               AS low_attendance_flag,
        CASE WHEN COALESCE(cf.attendee_count, 0) < 2
             THEN true ELSE false END                               AS very_low_attendance_flag,

        -- ── Cost signals ──────────────────────────────────────────────────────
        e.speaker_fee,
        e.venue_cost,
        e.travel_reimbursement,
        e.total_program_cost,

        -- MEAL_003: per-head ceiling $100 (Nova Pharma dinner — rules.json)
        COALESCE(cf.meal_cost_per_attendee, 0.0)                    AS meal_cost_per_attendee,
        CASE WHEN COALESCE(cf.meal_cost_per_attendee, 0.0) > 100.0
             THEN true ELSE false END                               AS cost_per_head_over_limit,

        COALESCE(cf.venue_cost_pct_of_total, 0.0)                   AS venue_cost_pct_of_total,
        -- VENUE_001: max venue cost $3,000 (rules.json)
        CASE WHEN e.venue_cost > 3000.0
             THEN true ELSE false END                               AS high_venue_cost_flag,
        -- VENUE_002: max total program cost $8,000 (rules.json)
        CASE WHEN e.total_program_cost > 8000.0
             THEN true ELSE false END                               AS over_total_cost_ceiling_flag,

        -- ── Speaker fee FMV (SPEAKER_001: ceiling $3,500 — rules.json) ────────
        COALESCE(cf.speaker_fee_fmv_pct, 0.0)                       AS speaker_fee_fmv_pct,
        CASE WHEN e.speaker_fee > 3500.0
             THEN true ELSE false END                               AS speaker_fee_over_fmv_flag,

        -- ── Speaker repeat patterns ───────────────────────────────────────────
        -- SPEAKER_003: repeat threshold > 3 events/year (rules.json)
        -- SPEAKER_002: high repeat threshold > 6 events/year (rules.json)
        COALESCE(sw.events_same_speaker_year, 0)                    AS events_same_speaker_year,
        CASE WHEN COALESCE(sw.events_same_speaker_year, 0) > 3
             THEN true ELSE false END                               AS repeat_speaker_flag,
        CASE WHEN COALESCE(sw.events_same_speaker_year, 0) > 6
             THEN true ELSE false END                               AS high_repeat_speaker_flag,

        -- SPEAKER_005: rapid repeat if < 30 days since last event (rules.json)
        -- NULL for first event per speaker per year (expected — not a data issue)
        sw.days_since_last_event_same_speaker,
        CASE WHEN sw.days_since_last_event_same_speaker IS NOT NULL
              AND sw.days_since_last_event_same_speaker < 30
             THEN true ELSE false END                               AS rapid_repeat_flag,

        -- ── Attestation (ATTEST_001: min 80% signed — rules.json) ────────────
        COALESCE(cf.attendees_signed_pct, 0.0)                      AS attendees_signed_pct,
        CASE WHEN COALESCE(cf.attendees_signed_pct, 0.0) < 0.80
             THEN true ELSE false END                               AS missing_attestation_flag,

        -- ── Composite risk score (0-100, pre-ML) ─────────────────────────────
        -- Component weights:
        --   Attestation gap:   25 pts — missing signatures = compliance failure
        --   Meal cost overage: 25 pts — per-head excess vs $100 ceiling (MEAL_003)
        --   Venue cost:        20 pts — venue spend vs $3,000 ceiling (VENUE_001)
        --   Speaker FMV:       20 pts — fee vs $3,500 FMV ceiling (SPEAKER_001)
        --   Low attendance:    10 pts — < 3 attendees (SPEAKER_004)
        --
        -- cost_per_head_ratio and venue_cost_ratio capped at 1.0 to prevent
        -- a single extreme value from consuming the full score allocation.
        LEAST(100.0,
            -- Attestation gap: 1 - signed_pct scaled to 25 pts max
            LEAST(25.0,
                (1.0 - COALESCE(cf.attendees_signed_pct, 0.0)) * 25.0
            )
            -- Meal cost overage: (cost_per_head / 100) capped at 1.0, scaled to 25 pts
            + LEAST(25.0,
                LEAST(1.0, COALESCE(cf.meal_cost_per_attendee, 0.0) / 100.0) * 25.0
            )
            -- Venue cost: (venue_cost / 3000) capped at 1.0, scaled to 20 pts
            + LEAST(20.0,
                LEAST(1.0, e.venue_cost / 3000.0) * 20.0
            )
            -- Speaker FMV: (fee / 3500) capped at 1.0, scaled to 20 pts
            + LEAST(20.0,
                LEAST(1.0, COALESCE(cf.speaker_fee_fmv_pct, 0.0)) * 20.0
            )
            -- Low attendance: binary 10 pts
            + CASE WHEN COALESCE(cf.attendee_count, 0) < 3
                   THEN 10.0 ELSE 0.0 END
        )                                                           AS raw_event_risk_score,

        -- ── Metadata ──────────────────────────────────────────────────────────
        CAST(CURRENT_TIMESTAMP AS timestamp)                         AS mart_created_at

    FROM events_base e
    LEFT JOIN cost_features  cf ON e.event_id = cf.event_id
    LEFT JOIN speaker_window sw ON e.event_id = sw.event_id
)

SELECT * FROM final
