SELECT *
FROM {{ ref('stg_cms_general_payments') }}
WHERE is_target = true
