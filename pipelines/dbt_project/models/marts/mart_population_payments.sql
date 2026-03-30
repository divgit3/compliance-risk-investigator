SELECT *
FROM {{ ref('stg_cms_general_payments') }}
WHERE hcp_id IN (
    SELECT DISTINCT hcp_id
    FROM {{ ref('stg_cms_general_payments') }}
    WHERE is_target = true
)
