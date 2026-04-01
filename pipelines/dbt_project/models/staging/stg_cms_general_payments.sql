WITH source AS (
    SELECT * FROM {{ source('athena_raw', 'cms_open_payments') }}
),

-- company_mapping seed is DuckDB-only; inline the 6-row mapping for Athena
{% if target.type == 'athena' %}
company_map AS (
    SELECT *
    FROM (VALUES
        ('takeda',  'Nova Pharma Inc',      true,  false),
        ('janssen', 'Stratos Biosciences',  false, true),
        ('merck',   'Nexagen Sciences',     false, true),
        ('amgen',   'Pinnacle Biosciences', false, true),
        ('squibb',  'Halcyon Pharma Inc',   false, true),
        ('celgene', 'Halcyon Pharma Inc',   false, true)
    ) AS t (real_name_pattern, pseudo_name, is_target, is_competitor)
),
{% else %}
company_map AS (
    SELECT * FROM {{ ref('company_mapping') }}
),
{% endif %}

anonymized AS (
    SELECT
        -- Record identity
        record_id,
        -- program_year (bigint) column is corrupted in the source Parquet:
        -- some rows contain NDC codes instead of years, and 2022 rows have NULLs.
        -- Use partition_0 (S3 folder name: '2022', '2023', '2024') as the authoritative year.
        CAST(partition_0 AS BIGINT) AS program_year,

        -- Physician identity — fully redacted
        -- CMS renamed 'physician_*' to 'covered_recipient_*' in 2021 data onwards
        CONCAT('HCP_', CAST(covered_recipient_profile_id AS VARCHAR)) AS hcp_id,
        covered_recipient_profile_id                                  AS physician_profile_id,
        CAST(NULL AS VARCHAR) AS physician_first_name,
        CAST(NULL AS VARCHAR) AS physician_last_name,
        CAST(NULL AS VARCHAR) AS physician_npi,
        CAST(NULL AS VARCHAR) AS physician_address,
        CAST(NULL AS VARCHAR) AS physician_city,
        covered_recipient_specialty_1 AS physician_specialty,
        recipient_state               AS physician_state,
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
    -- Exclude teaching hospital rows (no covered_recipient_profile_id → null hcp_id)
    WHERE covered_recipient_type IN (
        'Covered Recipient Physician',
        'Covered Recipient Non-Physician Practitioner'
    )
)

SELECT * FROM anonymized
