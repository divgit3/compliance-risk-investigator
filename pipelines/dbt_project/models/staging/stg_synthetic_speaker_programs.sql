-- Staging model for NovaPharma synthetic speaker program records.
-- SYNTHETIC DATA — not real. Generated for portfolio demonstration only.
-- Full transformation happens in Task 6.

select *
from {{ source('raw', 'synthetic_speaker_programs') }}
