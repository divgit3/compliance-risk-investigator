-- Staging model for NovaPharma synthetic HCP interaction records.
-- SYNTHETIC DATA — not real. Generated for portfolio demonstration only.
-- Full transformation happens in Task 6.

select *
from {{ source('raw', 'synthetic_interactions') }}
