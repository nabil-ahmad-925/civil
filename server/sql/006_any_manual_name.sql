-- Run once in the Supabase SQL editor, on a database made with an older schema.sql.
-- A document without numbered headings is uploaded under a name the user types, so `manual` is no longer limited to
-- PPM, HDM and CADD. Only removes the old checks; no rows are changed or deleted.

alter table source_documents drop constraint if exists source_documents_manual_check;
alter table nodes            drop constraint if exists nodes_manual_check;
alter table figures          drop constraint if exists figures_manual_check;
alter table manual_tables    drop constraint if exists manual_tables_manual_check;
