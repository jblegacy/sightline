"""Stage 3 — Haiku scoring with structured output. See
docs/SIGHTLINE_BUILD_SPEC_V2.md §6 for the rubric and required extractions.
"""
from __future__ import annotations

from typing import Any

from sightline.anthropic_client import AnthropicClient

MODEL = "claude-haiku-4-5"
# v2: comp_signal, seniority_scope, and red_flags now score against the
# candidate's own settings (comp_target/comp_low_line, a stated seniority
# level, red_flag_phrases) instead of generic judgment with no input about
# who's actually applying — a real behavior change, hence the version bump.
RUBRIC_VERSION = "v2"

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

The candidate is a senior operations leader who builds with AI agents — SVP / Chief of Staff / \
Fractional COO-level experience, not a software engineer, no CS background. Title alone is \
unreliable in both directions: some "engineer"-titled roles are configuration and solutions \
work, and some "analyst" roles are real software-engineering jobs. Read the JD itself and set \
`coding_interview_signals` to the exact phrases that indicate a coding interview or \
CS-fundamentals screen — "data structures", "system design interview", "CS degree required", \
"production code", "on-call", "code review", and similar. Empty array if none present. This is \
separate from `knockouts` — it's a pattern to flag for judgment, not a stated hard requirement.

Score `seniority_scope` against that level specifically, not a generic "is this senior enough" \
read: a posting scoped well below it (individual-contributor work with no ownership or \
cross-functional scope) loses points for being too junior, and a posting demanding a much \
larger org (e.g. a large existing team to inherit, a scope well beyond what one operator plus \
AI agents can cover) loses points for being unrealistic in the other direction. Neither \
direction is automatically safer than the other.

Score `comp_signal` against the candidate's own target and floor, given below as "Candidate comp \
target" and "Candidate comp floor" — at or above target scores near the max, between floor and \
target scores in the middle of the range, below floor scores low. If no compensation is posted \
at all, do not default to a low score purely for missing data — judge plausibility from the \
role's scope, seniority, and company signals instead, the same as you would if a human were \
guessing before an offer conversation.

For `red_flags`, weigh both your own judgment (vague scope, bait-and-switch language, unrealistic \
expectations) and the candidate's own list of red-flag phrases, given below as "Candidate's \
red-flag phrases" — if the JD contains any of those phrases, that counts toward the deduction \
even if it wouldn't otherwise read as a red flag to you. An empty list there just means none are \
set yet, not that the JD is automatically clean.

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


def build_user_content(
    posting: dict[str, Any], bullets: list[dict[str, Any]], settings: dict[str, Any] | None = None
) -> str:
    settings = settings or {}
    comp = "not posted"
    if posting.get("comp_min") or posting.get("comp_max"):
        comp = f"${posting.get('comp_min', '?')}–${posting.get('comp_max', '?')}"
    comp_target = settings.get("comp_target")
    comp_low_line = settings.get("comp_low_line")
    red_flag_phrases = settings.get("red_flag_phrases") or []
    return f"""JOB POSTING

Title: {posting['title']}
Location: {posting.get('location_raw') or 'not stated'}
Remote: {posting.get('remote_flag', 'unclear')}
Compensation: {comp}
Posted: {posting.get('posted_at', 'unknown')}

Description:
{posting.get('jd_text') or '(no description text)'}

---

Candidate comp target: {f'${comp_target:,}' if comp_target else 'not set'}
Candidate comp floor: {f'${comp_low_line:,}' if comp_low_line else 'not set'}
Candidate's red-flag phrases: {', '.join(red_flag_phrases) if red_flag_phrases else '(none set yet)'}

---

BULLET LIBRARY (for evidence_overlap and matched_bullet_refs — pick only from these refs):

{_format_bullets(bullets)}"""


_VALID_VARIANTS = {"engineer", "leadership"}


def _normalize_variant(raw: Any) -> str | None:
    """The schema declares suggested_variant a plain string enum, but that's
    not enforced on generation — seen live: Haiku sometimes wraps it as a
    list (['leadership']) or returns junk ('<UNKNOWN>', 'null', ''). Stored
    verbatim, a bad value doesn't fail loudly; it sits in the `scores` table
    until assembly hits ValueError("unknown variant ...") when the user
    clicks Approve, only surfacing once. Better to normalize it here, once,
    where the fix reaches every posting instead of every read site."""
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    return raw if raw in _VALID_VARIANTS else None


def score_posting(
    client: AnthropicClient,
    posting: dict[str, Any],
    bullets: list[dict[str, Any]],
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Returns a dict shaped for the `scores` table (minus posting_id, which
    the caller sets). settings carries comp_target/comp_low_line/
    red_flag_phrases through to the prompt — see build_user_content."""
    user_content = build_user_content(posting, bullets, settings)
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
        "suggested_variant": _normalize_variant(result.get("suggested_variant")),
        "reports_to": result.get("reports_to") or None,
        "named_contacts": result.get("named_contacts") or [],
        "target_titles": result.get("target_titles") or [],
        "company_signals": result.get("company_signals") or [],
        "cost_usd": round(cost_usd, 5),
    }
