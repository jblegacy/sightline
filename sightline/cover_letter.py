"""Cover letter generation — prose, not bullet selection, so this is a
different category from resume assembly (CLAUDE.md rule 1 is about resume
bullet text specifically). Same discipline as outreach drafts and the
answer workbench: grounded only in verified bullets and the posting's real
details, never inventing an achievement. Echoes the same bullets already
selected for this posting's resume so the two documents tell one story.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any

from docx import Document
from docx.shared import Inches, Pt

from sightline.anthropic_client import AnthropicClient
from sightline.assembly import CONTACT, NAME
from sightline.voice import VOICE_RULES, voice_reference

MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """You write a cover letter for a job application, grounded ONLY in the \
candidate's verified resume bullets given below plus the real details of this posting. Never \
invent an achievement, number, or outcome that isn't in the verified bullets.

Recruiters scan hundreds of these. Target 150-220 words total, including the bullet list below \
— a 30,000-foot pitch, not a case file. Every sentence you'd cut from a first draft, cut.

Confident, not cocky. State what you did and let it speak for itself — never tell the reader \
what the role "actually" is, what they're "actually" asking for beneath the posting's own words, \
or frame yourself as seeing something about the job that they don't. Never compare yourself to \
other candidates ("further than most candidates," "unlike other applicants") — you have no idea \
who else applied, and the claim reads as arrogant rather than as evidence.

Structure, in this order:
1. Opening — ONE sentence, plain and direct: "I'm excited to apply for the [Role] at [Company]" \
(this specific opener is fine and overrides VOICE RULES 6, 11, and 12 below for this one line \
only — the candidate has confirmed it, against real examples, as the opener he wants). What's \
NOT fine: restating what the role is or does back to the reader ("This role is about X, and I've \
been doing Y") — they wrote the posting, they know what it is; don't explain their own job to \
them. No "I'm confident I'm the ideal candidate" either — that's a conclusion about yourself, \
not a fact.
2. Context — ONE, at most two, sentences setting up the achievements below (what you do day to \
day, in plain terms).
3. A bullet list of 2-4 concrete, quantified wins, each one a close paraphrase or near-verbatim \
excerpt of one of the verified bullets below — never a new number, outcome, or claim. Pick \
whichever bullets best match the JD's most central, most-detailed ask; you don't need to touch \
every responsibility category it lists. Each bullet line starts with "- " and is one line, no \
sub-clauses.
4. Close — ONE sentence, forward-looking, on what you'd bring from here.

Never name a gap, missing skill, or unmet requirement anywhere in the letter — this overrides \
VOICE RULE 7 below. A cover letter is a pitch, not a disclosure; gaps get discussed in the \
interview if they come up, not volunteered here. Pick bullets and framing that put the strongest \
foot forward and simply don't mention what isn't there.

No corporate throat-clearing, no restating the resume verbatim, no invented personality or \
passion claims, no sentence that could be pasted into any other cover letter unchanged. Return \
only the letter body — opening, context, bullet list, close — no salutation or signature, those \
are added separately. Separate the opening+context block, the bullet list, and the close with \
blank lines.

{voice_rules}

Below are additional real samples of the candidate's own writing, for rhythm and word choice \
only — do not copy their content or reuse their sentences, these are a different story for a \
different question:

VOICE REFERENCE (candidate's own writing):
{voice_reference}

Bullets already selected for this posting's resume — echo this same emphasis:
{selected_bullets}

Rest of the verified bullet library, for supporting reference only:
{other_bullets}"""


def _format_bullets(bullets: list[dict[str, Any]]) -> str:
    return "\n".join(f"- [{b['ref']}] {b['text']}" for b in bullets) or "(none)"


def build_user_content(posting: dict[str, Any], score: dict[str, Any]) -> str:
    company = (posting.get("companies") or {}).get("name", "this company")
    # 3000 chars was cutting off later JD sections (qualifications, specific
    # responsibility categories) on longer postings — the new instruction to
    # work through the JD's own stated responsibility categories only works
    # if those categories are actually still in view.
    jd_excerpt = (posting.get("jd_text") or "")[:6000]
    return f"""Posting: {posting.get('title')} at {company}

JD:
{jd_excerpt}

Score rationale: {score.get('rationale') or ''}
Keywords to mirror: {', '.join(score.get('keywords') or [])}
Gaps to prepare for: {', '.join(score.get('unmet_requirements') or [])}
Company signals: {', '.join(score.get('company_signals') or [])}"""


def greeting_for(score: dict[str, Any]) -> str:
    """Use a real named contact the scorer already extracted from the JD,
    rather than a generic "Dear Hiring Team" — personalizing the greeting
    is consistently named as a best practice, and Sightline already has
    this data most of the time without needing any new lookup."""
    contacts = score.get("named_contacts") or []
    if contacts and contacts[0].get("name"):
        return f"Dear {contacts[0]['name']},"
    return "Dear Hiring Team,"


def generate_cover_letter(
    client: AnthropicClient,
    posting: dict[str, Any],
    score: dict[str, Any],
    bullets: list[dict[str, Any]],
    selected_bullet_refs: list[str],
    answers: list[dict[str, Any]] | None = None,
) -> tuple[str, float]:
    verified = [b for b in bullets if b.get("status") == "verified"]
    selected = [b for b in verified if b["ref"] in selected_bullet_refs]
    other = [b for b in verified if b["ref"] not in selected_bullet_refs]
    system = SYSTEM_PROMPT.format(
        voice_rules=VOICE_RULES, voice_reference=voice_reference(answers or []),
        selected_bullets=_format_bullets(selected), other_bullets=_format_bullets(other),
    )
    user_content = build_user_content(posting, score)
    text, cost = client.chat_call(
        model=MODEL, system=system, messages=[{"role": "user", "content": user_content}], max_tokens=1500,
    )
    text = text.strip()
    # Found live: a truncated/empty response was silently saved as the
    # cover letter and uploaded to storage — never let that happen quietly.
    if len(text) < 50:
        raise ValueError(f"cover letter generation returned no usable text ({len(text)} chars)")
    return text, cost


def render_cover_letter_docx(text: str, company: str, title: str, greeting: str = "Dear Hiring Team,") -> bytes:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    style.paragraph_format.line_spacing = 1.15
    for s in doc.sections:
        s.left_margin = s.right_margin = s.top_margin = s.bottom_margin = Inches(1.0)

    def para(t: str, *, size: float = 11, bold: bool = False, after: float = 10) -> None:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(after)
        r = p.add_run(t)
        r.font.size = Pt(size)
        r.bold = bold

    para(NAME, size=14, bold=True, after=1)
    para(CONTACT, size=9.5, after=16)
    para(datetime.now(timezone.utc).strftime("%B %d, %Y"), size=10.5, after=16)
    para(f"Re: {title} at {company}", size=10.5, bold=True, after=12)
    para(greeting, size=11, after=10)

    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if lines and all(ln.startswith("- ") for ln in lines):
            for ln in lines:
                p = doc.add_paragraph(ln[2:].strip(), style="List Bullet")
                p.paragraph_format.space_after = Pt(4)
                for r in p.runs:
                    r.font.size = Pt(11)
            doc.paragraphs[-1].paragraph_format.space_after = Pt(10)
        else:
            para(block, size=11, after=10)

    para("Sincerely,", size=11, after=2)
    para(NAME, size=11, after=0)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
