-- Staging model for NovaPharma synthetic HCP master records.
-- SYNTHETIC DATA — not real. Contains specialty, state, fmv_tier, profile per HCP.
{% if target.name == 'athena' %}
select * from {{ source('athena_synthetic', 'hcp_master') }}
{% else %}
-- DuckDB: hcp_master is loaded directly; no staging source needed
select 
    CAST(NULL AS VARCHAR) AS hcp_id,
    CAST(NULL AS VARCHAR) AS specialty,
    CAST(NULL AS VARCHAR) AS state
where 1=0
{% endif %}
