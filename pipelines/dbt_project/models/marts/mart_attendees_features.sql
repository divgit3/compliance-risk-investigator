-- Feature mart for speaker program attendees.
-- ALL columns EXCEPT violation flags — used as anomaly detection model input.
-- Violation flags are excluded here to prevent label leakage into the pipeline.

SELECT
    attendee_id,
    event_id,
    attendee_hcp_id,
    attendee_type,
    attendee_practice_id,
    attendee_practice_city,
    same_office_as_speaker,
    attendee_specialty,
    attendee_state,
    meal_provided,
    meal_value,
    signed_attestation,
    repeat_attendee_same_topic,
    times_attended_same_topic,
    program_year,
    synthetic_data_flag
FROM {{ ref('stg_synthetic_attendees') }}
