-- Staging model for NovaPharma synthetic speaker program attendee records.
-- SYNTHETIC DATA — not real.
{% if target.name == 'athena' %}
select * from {{ source('athena_synthetic', 'speaker_program_attendees') }}
{% else %}
select * from {{ source('raw', 'synthetic_attendees') }}
{% endif %}
