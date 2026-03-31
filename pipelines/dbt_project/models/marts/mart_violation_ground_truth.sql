-- Ground truth mart — violation flags ONLY, joined to record IDs.
-- Used EXCLUSIVELY for Phase 2 model validation.
-- NEVER used as model input — doing so would constitute label leakage.

WITH interactions AS (
    SELECT
        interaction_id  AS record_id,
        hcp_id,
        program_year,
        'interaction'   AS record_type,
        violation_types,
        violation_severity,
        is_violation
    FROM {{ ref('stg_synthetic_interactions') }}
),

speaker_events AS (
    SELECT
        event_id        AS record_id,
        speaker_hcp_id  AS hcp_id,
        program_year,
        'speaker_event' AS record_type,
        violation_types,
        violation_severity,
        is_violation
    FROM {{ ref('stg_synthetic_speaker_programs') }}
)

SELECT * FROM interactions
UNION ALL
SELECT * FROM speaker_events
