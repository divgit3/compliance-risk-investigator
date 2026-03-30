-- Staging model for CMS Open Payments general payments data (public).
-- Passes through raw source columns unchanged.
-- Full transformation happens in Task 6.

select *
from {{ source('raw', 'cms_general_payments') }}
