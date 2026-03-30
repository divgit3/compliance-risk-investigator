WITH source AS (
    SELECT * FROM {{ source('raw', 'cms_open_payments') }}
),

company_map AS (
    SELECT * FROM {{ ref('company_mapping') }}
),

anonymized AS (
    SELECT
        -- Record identity
        record_id,
        program_year,

        -- Physician identity — fully redacted
        CONCAT('HCP_', physician_profile_id) AS hcp_id,
        physician_profile_id,
        NULL AS physician_first_name,
        NULL AS physician_last_name,
        NULL AS physician_npi,
        NULL AS physician_address,
        NULL AS physician_city,
        physician_specialty,
        physician_state,
        recipient_zip_code,

        -- Company identity
        -- 5 companies pseudonymized; all others keep real name (public CMS data)
        COALESCE(
            cm.pseudo_name,
            applicable_manufacturer_or_applicable_gpo_making_payment_name
        ) AS company_name,
        COALESCE(cm.is_target, false)     AS is_target,
        COALESCE(cm.is_competitor, false) AS is_competitor,
        CASE
            WHEN cm.pseudo_name IS NULL
             AND applicable_manufacturer_or_applicable_gpo_making_payment_name IS NOT NULL
            THEN true
            ELSE false
        END AS is_population_only,

        -- Payment details
        total_amount_of_payment_usdollars                    AS payment_amount,
        date_of_payment,
        nature_of_payment_or_transfer_of_value               AS nature_of_payment,
        form_of_payment_or_transfer_of_value                 AS payment_form,
        number_of_payments_included_in_total_amount          AS payment_count,

        -- Product context
        name_of_drug_or_biological_or_device_or_medical_supply_1  AS product_name_1,
        indicate_drug_or_biological_or_device_or_medical_supply_1  AS product_type_1,

        -- Metadata
        payment_publication_date,
        dispute_status_for_publication

    FROM source s
    LEFT JOIN company_map cm
        ON LOWER(
            s.applicable_manufacturer_or_applicable_gpo_making_payment_name
        ) LIKE '%' || cm.real_name_pattern || '%'
)

SELECT * FROM anonymized
