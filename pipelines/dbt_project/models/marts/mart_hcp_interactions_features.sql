-- Feature mart for HCP interactions.
-- ALL columns EXCEPT violation flags — used as anomaly detection model input.
-- Violation flags are excluded here to prevent label leakage into the pipeline.

SELECT
    interaction_id,
    hcp_id,
    interaction_date,
    interaction_type,
    rep_id,
    rep_territory,
    product_discussed,
    interaction_city,
    interaction_state,
    practice_id,
    practice_city,
    meal_cost,
    attendee_count,
    fmv_rate_used,
    fmv_tier,
    fmv_benchmark,
    fmv_approved,
    alcohol_provided,
    payment_amount,
    annual_total_ytd,
    compliance_reviewed,
    compliance_flag,
    business_rationale,
    cms_total_this_year,
    is_reconciliation_anomaly,
    program_year,
    synthetic_data_flag
FROM {{ ref('stg_synthetic_interactions') }}
