-- Feature mart for speaker program events.
-- ALL columns EXCEPT violation flags — used as anomaly detection model input.
-- Violation flags are excluded here to prevent label leakage into the pipeline.

SELECT
    event_id,
    event_date,
    speaker_hcp_id,
    speaker_practice_id,
    speaker_practice_city,
    speaker_specialty,
    speaker_tier,
    program_topic,
    venue_name,
    venue_type,
    venue_city,
    venue_state,
    venue_cost,
    attendee_count,
    speaker_fee,
    fmv_benchmark,
    fmv_exceeded,
    travel_reimbursement,
    total_program_cost,
    product_featured,
    alcohol_provided,
    compliance_approved,
    repeat_speaker,
    times_spoke_this_year,
    program_topic_repeat_count,
    annual_speaker_compensation,
    program_year,
    synthetic_data_flag
FROM {{ ref('stg_synthetic_speaker_programs') }}
