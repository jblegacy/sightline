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
invent an achievement, number, or outcome that isn't in the verified bullets. If the posting \
has real gaps against the candidate's background, acknowledge them honestly rather than \
talking around them — don't oversell.

Target 250-400 words total, one page. Hiring managers spend under 30 seconds on a cover \
letter — a tight, specific 300 words consistently beats a thorough 700.

Before writing, identify the JD's actual stated responsibility categories (postings are often \
structured in named sections, e.g. "Business Process Analysis" / "Agent Strategy & Design" / \
"Adoption Measurement" — use whatever the JD's own structure is). Do not silently skip the \
category the candidate matches worst, even if it means the letter leans more on what's honestly \
missing there than on a strong bullet — a letter that ignores the JD's single most-detailed \
responsibility section reads as not having read the posting, which is worse than admitting a gap.

Write 3-4 tight paragraphs, separated by blank lines:
1. Opening hook — name the role and one concrete, specific reason it's a fit for THIS posting \
and THIS company (reference something real from the JD or company signals, not generic \
enthusiasm — "I'm excited to apply" tells the reader nothing they can't assume).
2. One to two body paragraphs connecting 2-3 of the verified bullets below directly to what \
this posting is actually asking for, covering the JD's actual responsibility categories rather \
than only the ones with the easiest bullet match. Don't just restate accomplishments — show \
what they mean for THIS team: what you'd be able to contribute or unblock given what they're \
hiring for. If there's a real, material gap against a category the JD clearly cares about \
(including one with no matching bullet at all), name it plainly in one sentence rather than \
omitting it — specificity and honesty read as more credible than a resume rehash, and recruiters \
increasingly say generic, template-shaped letters are what actually gets an application \
rejected, not the use of an AI drafting tool.
3. Short close — forward-looking (what you'd bring to the team from here), not a recap of the \
gap you just named and not "I look forward to hearing from you" filler.

No corporate throat-clearing, no restating the resume verbatim, no invented personality or \
passion claims, no sentence that could be pasted into any other cover letter unchanged. Return \
only the letter body paragraphs, no salutation or signature — those are added separately.

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
    return text.strip(), cost


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
        if block:
            para(block, size=11, after=10)

    para("Sincerely,", size=11, after=2)
    para(NAME, size=11, after=0)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
