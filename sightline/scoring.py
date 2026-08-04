"""Stage 3 — Haiku scoring with structured output. See
docs/SIGHTLINE_BUILD_SPEC_V2.md §6 for the rubric and required extractions.
"""
from __future__ import annotations

from typing import Any

from sightline.anthropic_client import AnthropicClient

MODEL = "claude-haiku-4-5"
RUBRIC_VERSION = "v1"

# (dimension key, max points) — matches prototype/sightline-dashboard.html's
# DIMS array exactly, since that prototype is the UI contract this scores for.
DIMENSIONS: list[tuple[str, int]] = [
    ("role_fit", 25),
    ("evidence_overlap", 20),
    ("seniority_scope", 15),
    ("remote_authenticity", 15),
    ("comp_signal", 15),
    ("company_stage_fit", 10),
    ("red_flags", -30),
]

def _rubric_lines() -> str:
    lines = []
    for key, max_pts in DIMENSIONS:
        rng = f"-{abs(max_pts)} to 0" if max_pts < 0 else f"0 to {max_pts}"
        lines.append(f"- {key}: {rng} points")
    return "\n".join(lines)


SYSTEM_PROMPT = f"""You score job postings for a job-search pipeline, against a fixed rubric. \
You are not writing resume content or making application decisions — only scoring, extracting, \
and flagging.

Score each dimension within its own point range, not a generic 0-10 scale:

{_rubric_lines()}

`total` is the exact sum of all seven dimension scores (the max possible total is \
{sum(m for _, m in DIMENSIONS if m > 0)}, before any red_flags deduction).

Only flag HARD requirements as knockouts — required years of experience, required domain, \
required credential, mandatory onsite days. Do NOT flag "preferred" or "nice to have" language; \
flagging soft preferences destroys the signal this field is supposed to carry.

The candidate is an operations leader who builds with AI agents — not a software engineer, no \
CS background. Title alone is unreliable in both directions: some "engineer"-titled roles are \
configuration and solutions work, and some "analyst" roles are real software-engineering jobs. \
Read the JD itself and set `coding_interview_signals` to the exact phrases that indicate a \
coding interview or CS-fundamentals screen — "data structures", "system design interview", \
"CS degree required", "production code", "on-call", "code review", and similar. Empty array if \
none present. This is separate from `knockouts` — it's a pattern to flag for judgment, not a \
stated hard requirement.

`matched_bullet_refs` must only contain refs that appear in the provided bullet library — \
never invent a ref. Pick bullets whose tags or content plausibly overlap this posting's \
requirements; this is for keyword-matching guidance only, not a final selection.

`rationale` is required and must be substantive — a score without a rationale is a number \
with good manners, not something a person can act on."""

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "dimensions": {
            "type": "object",
            "properties": {
                k: (
                    {"type": "integer", "minimum": max_pts, "maximum": 0}
                    if max_pts < 0
                    else {"type": "integer", "minimum": 0, "maximum": max_pts}
                )
                for k, max_pts in DIMENSIONS
            },
            "required": [k for k, _ in DIMENSIONS],
        },
        "total": {"type": "integer"},
        "rationale": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "matched_bullet_refs": {"type": "array", "items": {"type": "string"}},
        "unmet_requirements": {"type": "array", "items": {"type": "string"}},
        "knockouts": {"type": "array", "items": {"type": "string"}},
        "coding_interview_signals": {"type": "array", "items": {"type": "string"}},
        "suggested_variant": {"type": "string", "enum": ["engineer", "leadership"]},
        "reports_to": {"type": "string", "description": "Verbatim if stated; empty string if not stated in the posting."},
        "named_contacts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "title": {"type": "string"},
                    "context": {"type": "string"},
                },
                "required": ["name", "title", "context"],
            },
        },
        "target_titles": {"type": "array", "items": {"type": "string"}},
        "company_signals": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "dimensions", "total", "rationale", "keywords", "matched_bullet_refs",
        "unmet_requirements", "knockouts", "coding_interview_signals", "suggested_variant",
        "reports_to", "named_contacts", "target_titles", "company_signals",
    ],
}


def _format_bullets(bullets: list[dict[str, Any]]) -> str:
    lines = []
    for b in bullets:
        variants = "/".join(b["variants"])
        tags = ", ".join(b["tags"])
        lines.append(f"- {b['ref']} [{variants}] ({tags}): {b['text']}")
    return "\n".join(lines)


def build_user_content(posting: dict[str, Any], bullets: list[dict[str, Any]]) -> str:
    comp = "not posted"
    if posting.get("comp_min") or posting.get("comp_max"):
        comp = f"${posting.get('comp_min', '?')}–${posting.get('comp_max', '?')}"
    return f"""JOB POSTING

Title: {posting['title']}
Location: {posting.get('location_raw') or 'not stated'}
Remote: {posting.get('remote_flag', 'unclear')}
Compensation: {comp}
Posted: {posting.get('posted_at', 'unknown')}

Description:
{posting.get('jd_text') or '(no description text)'}

---

BULLET LIBRARY (for evidence_overlap and matched_bullet_refs — pick only from these refs):

{_format_bullets(bullets)}"""


def score_posting(
    client: AnthropicClient,
    posting: dict[str, Any],
    bullets: list[dict[str, Any]],
) -> dict[str, Any]:
    """Returns a dict shaped for the `scores` table (minus posting_id, which
    the caller sets)."""
    user_content = build_user_content(posting, bullets)
    result, cost_usd = client.structured_call(
        model=MODEL,
        system=SYSTEM_PROMPT,
        user_content=user_content,
        tool_name="submit_score",
        tool_description="Submit the rubric score and extracted fields for this posting.",
        input_schema=INPUT_SCHEMA,
    )
    # Recompute total ourselves rather than trust the model's arithmetic —
    # each dimension being individually correct doesn't guarantee the sum is.
    computed_total = sum(result["dimensions"].values())
    # The schema marks these required, but tool-use generation completeness
    # isn't guaranteed — a long JD can occasionally cause Haiku to drop one.
    # These are extracted signal, not the score itself (dimensions/total/
    # rationale stay hard-required below): defaulting is safer than losing
    # the whole score over one missing extraction field.
    return {
        "rubric_version": RUBRIC_VERSION,
        "model": MODEL,
        "total": computed_total,
        "dimensions": result["dimensions"],
        "rationale": result["rationale"],
        "keywords": result.get("keywords") or [],
        "matched_bullet_ids": result.get("matched_bullet_refs") or [],
        "unmet_requirements": result.get("unmet_requirements") or [],
        "knockouts": result.get("knockouts") or [],
        "coding_interview_signals": result.get("coding_interview_signals") or [],
        "suggested_variant": result.get("suggested_variant"),
        "reports_to": result.get("reports_to") or None,
        "named_contacts": result.get("named_contacts") or [],
        "target_titles": result.get("target_titles") or [],
        "company_signals": result.get("company_signals") or [],
        "cost_usd": round(cost_usd, 5),
    }
