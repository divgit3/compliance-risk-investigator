-- Staging model for NovaPharma synthetic speaker program records.
-- SYNTHETIC DATA — not real. Generated for portfolio demonstration only.
{% if target.name == 'athena' %}
select * from {{ source('athena_synthetic', 'speaker_program_events') }}
{% else %}
select * from {{ source('raw', 'synthetic_speaker_programs') }}
{% endif %}
