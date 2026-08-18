"""Outreach drafts. See docs/SIGHTLINE_BUILD_SPEC_V2.md §7-8 and CLAUDE.md
rules 3-4: drafts only, never sent automatically, and no automation against
LinkedIn — the search link is a URL a human clicks, nothing more.

Unlike resume assembly, these drafts ARE new prose written by the model —
that's the point of this stage. What stays gated is the one metric the
draft is allowed to cite: it must come from the bullet library, and that
bullet still has to clear the provenance validator before it's handed to
the model, for the same reason a resume claim does.
"""
from __future__ import annotations

import urllib.parse
from typing import Any

from sightline.anthropic_client import AnthropicClient
from sightline.db import SightlineDB
from sightline.provenance import Bullet, ProvenanceError, assert_shippable
from sightline.voice import VOICE_RULES, voice_reference

DRAFT_MODEL = "claude-sonnet-5"

DRAFT_SYSTEM_PROMPT = """You write outreach drafts for a job candidate reaching out about a \
specific posting. The candidate copies these by hand into LinkedIn or their own email client — \
nothing here is ever sent automatically, and you are not the one sending it.

Model every draft on this confirmed, real example — the candidate sent it and wants every future \
draft to have exactly this shape:
"Hi Sri — just applied for the AI Enablement & Process Automation Lead role. Excited about the \
chance to help bring Copilot adoption and workflow automation to life at Liquidity Services."

That's the whole shape: a greeting by first name, a plain statement that you just applied — \
naming the exact job title from the posting — and ONE short sentence of specific, concrete \
excitement about the actual work. Nothing else. Short and to the point beats thorough here, \
every time.

Follow these rules exactly:
1. Open with "Hi [First name] —" then state plainly that you just applied for the exact job \
title from the posting (use the posting's own title, not a paraphrase).
2. ONE short sentence after that: specific, concrete excitement about the real work — what \
you'd get to help with — tied to actual language from the JD or the company signal given. Not \
generic ("excited about this opportunity"), specific ("excited to help automate X").
3. No permission-based ask, no "worth a conversation," no calendar link. Applying and stating \
real, specific interest IS the message — don't tack on a call-to-action the model example \
doesn't have.
4. The metric bullet given below is optional, not required. Use it only if it fits in one \
additional short clause without breaking the short-and-sweet shape above — most of the time, \
leave it out entirely, same as the model example.
5. Subject line (email only): specific, under {subject_max_words} words, never "Seeking \
Opportunities" or anything that reads like a bulk send.
6. Plain text only — no HTML, no tracking language, no attachment mentioned.
7. Write three variants, ALL following the exact shape above (not three different structures — \
one structure, three lengths): a LinkedIn connection note (well under {note_max_chars} \
characters), a LinkedIn message (around {message_max_words} words — a sentence or two longer \
than the note at most, never a different, longer pitch), and an email body (around \
{email_max_words} words, same shape, with its own subject line). Sign as "James".

{voice_rules}

These are short enough that the sentence-rhythm rule above mostly doesn't apply — prioritize the \
banned-word list, concrete nouns, and matching the model example's directness over rhythm \
variation.

None of these three drafts should name a gap, missing skill, or unmet requirement. Outreach is \
part of applying, same as the cover letter; gaps get discussed in the interview if they come up, \
not volunteered before it.

Real samples of the candidate's own writing, for word choice and tone only — do not copy their \
content, these are a different context entirely:
{voice_reference}"""

DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "note": {"type": "string"},
        "message": {"type": "string"},
        "subject": {"type": "string"},
        "email": {"type": "string"},
    },
    "required": ["note", "message", "subject", "email"],
}


def linkedin_search_url(company: str, target_titles: list[str]) -> str:
    """A pre-filled people-search link a human clicks — never automated
    against LinkedIn, per CLAUDE.md rule 4."""
    q = f"{company} {target_titles[0]}" if target_titles else company
    return f"https://www.linkedin.com/search/results/people/?keywords={urllib.parse.quote(q)}"


def select_metric_bullet(
    bullets: list[dict[str, Any]], variant: str, keywords: list[str]
) -> dict[str, Any] | None:
    """Highest tag-overlap bullet for this variant — the one proof point the
    draft is allowed to cite. None if the library has nothing for this
    variant at all."""
    kw = [k.lower() for k in keywords]

    def hit(tags: list[str]) -> int:
        return sum(1 for t in tags if any(k in t.lower() or t.lower() in k for k in kw))

    candidates = [b for b in bullets if variant in (b.get("variants") or [])]
    if not candidates:
        return None
    return max(candidates, key=lambda b: hit(b.get("tags") or []))


def generate_drafts(
    client: AnthropicClient,
    posting: dict[str, Any],
    score: dict[str, Any],
    target_name: str,
    target_title: str | None,
    metric_bullet: dict[str, Any],
    limits: dict[str, int],
    answers: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, str], float]:
    company = (posting.get("companies") or {}).get("name", "this company")
    signals = score.get("company_signals") or []
    signal = signals[0] if signals else "(none extracted — open on the role itself)"
    system = DRAFT_SYSTEM_PROMPT.format(
        subject_max_words=limits.get("email_subject_max_words", 10),
        note_max_chars=limits.get("linkedin_note_max_chars", 300),
        message_max_words=limits.get("linkedin_message_max_words", 60),
        email_max_words=limits.get("email_max_words", 60),
        voice_rules=VOICE_RULES, voice_reference=voice_reference(answers or []),
    )
    user_content = f"""Target: {target_name}, {target_title or 'title unknown'}
Posting: {posting.get('title')} at {company}
Company signal to open with: {signal}
Metric to reference — this is the candidate's own achievement, at a previous employer, not \
something the target or their company did (use this claim, not a different one): {metric_bullet['text']}
Reports to (if relevant to mention): {score.get('reports_to') or 'not stated'}"""
    result, cost_usd = client.structured_call(
        model=DRAFT_MODEL,
        system=system,
        user_content=user_content,
        tool_name="submit_drafts",
        tool_description="Submit the three outreach draft variants.",
        input_schema=DRAFT_SCHEMA,
        max_tokens=1200,
    )
    return result, cost_usd


def assemble_outreach(
    db: SightlineDB,
    anthropic: AnthropicClient,
    posting_id: int,
    target_name: str,
    target_title: str | None = None,
    target_linkedin_url: str | None = None,
    variant: str | None = None,
) -> dict[str, Any]:
    """Selects the one bullet the drafts may cite, gates it through the
    provenance validator (not caught to continue past it, only to log the
    block before re-raising), generates the three drafts, and upserts the
    `outreach` row for this posting."""
    if not target_name:
        raise ValueError("target_name is required")

    posting = db.get_posting(posting_id)
    scores = posting.get("scores") or []
    if not scores:
        raise ValueError(f"posting {posting_id} has not been scored yet")
    score = scores[0]
    variant = variant or score.get("suggested_variant") or "engineer"

    bullets = db.get_bullets_full()
    metric_bullet = select_metric_bullet(bullets, variant, score.get("keywords") or [])
    if metric_bullet is None:
        raise ValueError(f"no bullets available for variant {variant!r} — nothing to reference")

    try:
        assert_shippable([
            Bullet(
                ref=metric_bullet["ref"], text=metric_bullet["text"],
                provenance=metric_bullet["provenance"], status=metric_bullet["status"],
            )
        ])
    except ProvenanceError as e:
        db.log_event(
            entity_type="posting", entity_id=posting_id, event="outreach_blocked",
            payload={"reason": str(e), "metric_ref": metric_bullet["ref"]},
        )
        raise

    settings = db.get_settings()
    limits = {
        "linkedin_note_max_chars": settings.get("linkedin_note_max_chars", 300),
        "linkedin_message_max_words": settings.get("linkedin_message_max_words", 60),
        "email_max_words": settings.get("email_max_words", 60),
        "email_subject_max_words": settings.get("email_subject_max_words", 10),
    }
    answers = db.get_answers()
    drafts, cost_usd = generate_drafts(
        anthropic, posting, score, target_name, target_title, metric_bullet, limits, answers
    )

    row = db.upsert_outreach({
        "posting_id": posting_id,
        "target_name": target_name,
        "target_title": target_title,
        "target_linkedin_url": target_linkedin_url,
        "draft_linkedin_note": drafts["note"],
        "draft_linkedin_message": drafts["message"],
        "draft_email_subject": drafts["subject"],
        "draft_email_body": drafts["email"],
    })
    db.log_event(
        entity_type="outreach", entity_id=row["id"], event="drafts_generated",
        payload={
            "posting_id": posting_id,
            "metric_ref": metric_bullet["ref"],
            "cost_usd": round(cost_usd, 5),
        },
    )
    return row
