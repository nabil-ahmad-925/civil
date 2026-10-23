-- Run once in the Supabase SQL editor, on a database that already has the cross_refs table.
-- Allows references to documents that are not PPM, HDM or CADD (target_manual = 'OTHER', status = 'external').
-- Only widens the allowed values; no rows are changed or deleted.

alter table cross_refs drop constraint if exists cross_refs_target_manual_check;
alter table cross_refs add constraint cross_refs_target_manual_check
  check (target_manual in ('PPM', 'HDM', 'CADD', 'OTHER'));

alter table cross_refs drop constraint if exists cross_refs_status_check;
alter table cross_refs add constraint cross_refs_status_check
  check (status in ('unresolved', 'resolved', 'awaiting_cross', 'external'));
