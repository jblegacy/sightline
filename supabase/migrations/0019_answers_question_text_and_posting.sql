-- Answer Workbench redesign: today the table stores only a question_type
-- slug the candidate has to invent by hand, and no link to which
-- application first surfaced the question. Neither is enough to recognize
-- "you've been asked something like this before" against a new question's
-- actual wording, or to show which role it came from.
alter table answers add column question_text text;
alter table answers add column posting_id bigint references postings(id);
