-- Reworded per the new Workflow Automation resume (repositioned off
-- "engineer" framing) — text-only, refs/tags/provenance unchanged. Diffed
-- against the prior seed: only these 3 of the 19 engineer-variant bullets
-- changed; the rest are verbatim.

update bullets set text =
  'Specified and shipped an autonomous forecasting and decision-automation platform running live in production — 68 API endpoints, 540 configurable strategy profiles, ensemble modeling, and automated risk controls with real-time hedging — built end to end by directing AI coding agents.'
where ref = 'BL-025';

update bullets set text =
  'Designed and delivered data pipelines processing hundreds of thousands of records, including batch classification and enrichment through LLM APIs.'
where ref = 'BL-030';

update bullets set text =
  'Cut recurring AI operating cost on production workloads by restructuring how requests were batched and cached.'
where ref = 'BL-031';
