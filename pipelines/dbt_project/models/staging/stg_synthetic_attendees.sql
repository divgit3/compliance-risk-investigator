-- Staging model for NovaPharma synthetic speaker program attendee records.
-- SYNTHETIC DATA — not real. Generated for portfolio demonstration only.

select *
from {{ source('raw', 'synthetic_attendees') }}
