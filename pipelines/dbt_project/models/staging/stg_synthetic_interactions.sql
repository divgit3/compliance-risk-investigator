-- Staging model for NovaPharma synthetic HCP interaction records.
-- SYNTHETIC DATA — not real. Generated for portfolio demonstration only.
{% if target.name == 'athena' %}
select * from {{ source('athena_synthetic', 'hcp_interactions') }}
{% else %}
select * from {{ source('raw', 'synthetic_interactions') }}
{% endif %}
