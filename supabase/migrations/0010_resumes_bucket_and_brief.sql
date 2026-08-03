-- Phase 5 (assembly): private Storage bucket for generated .docx files, and
-- a column to hold the Sonnet-generated one-page brief for each variant.
-- See CLAUDE.md: "Storage bucket private, signed URLs only."

insert into storage.buckets (id, name, public)
values ('resumes', 'resumes', false)
on conflict (id) do nothing;

alter table variants add column brief text;
