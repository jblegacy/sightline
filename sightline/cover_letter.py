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

MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """You write a cover letter for a job application, grounded ONLY in the \
candidate's verified resume bullets given below plus the real details of this posting. Never \
invent an achievement, number, or outcome that isn't in the verified bullets. If the posting \
has real gaps against the candidate's background, acknowledge them honestly rather than \
talking around them — don't oversell.

Write 3-4 tight paragraphs, separated by blank lines: an opening that names the role and one \
concrete, specific reason it's a fit (not generic enthusiasm), a middle section connecting 2-3 \
of the verified bullets below directly to what this posting is actually asking for, and a short \
close. No corporate throat-clearing ("I am excited to apply..."), no restating the resume \
verbatim, no invented personality or passion claims. Plain, direct, specific — matching the \
voice of the verified material itself. Return only the letter body paragraphs, no salutation \
or signature — those are added separately.

Bullets already selected for this posting's resume — echo this same emphasis:
{selected_bullets}

Rest of the verified bullet library, for supporting reference only:
{other_bullets}"""


def _format_bullets(bullets: list[dict[str, Any]]) -> str:
    return "\n".join(f"- [{b['ref']}] {b['text']}" for b in bullets) or "(none)"


def build_user_content(posting: dict[str, Any], score: dict[str, Any]) -> str:
    company = (posting.get("companies") or {}).get("name", "this company")
    jd_excerpt = (posting.get("jd_text") or "")[:3000]
    return f"""Posting: {posting.get('title')} at {company}

JD:
{jd_excerpt}

Score rationale: {score.get('rationale') or ''}
Keywords to mirror: {', '.join(score.get('keywords') or [])}
Gaps to prepare for: {', '.join(score.get('unmet_requirements') or [])}
Company signals: {', '.join(score.get('company_signals') or [])}"""


def generate_cover_letter(
    client: AnthropicClient,
    posting: dict[str, Any],
    score: dict[str, Any],
    bullets: list[dict[str, Any]],
    selected_bullet_refs: list[str],
) -> tuple[str, float]:
    verified = [b for b in bullets if b.get("status") == "verified"]
    selected = [b for b in verified if b["ref"] in selected_bullet_refs]
    other = [b for b in verified if b["ref"] not in selected_bullet_refs]
    system = SYSTEM_PROMPT.format(
        selected_bullets=_format_bullets(selected), other_bullets=_format_bullets(other),
    )
    user_content = build_user_content(posting, score)
    text, cost = client.chat_call(
        model=MODEL, system=system, messages=[{"role": "user", "content": user_content}], max_tokens=900,
    )
    return text.strip(), cost


def render_cover_letter_docx(text: str, company: str, title: str) -> bytes:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    for s in doc.sections:
        s.left_margin = s.right_margin = s.top_margin = s.bottom_margin = Inches(1.0)

    def para(t: str, *, size: float = 11, bold: bool = False, after: float = 10) -> None:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(after)
        r = p.add_run(t)
        r.font.size = Pt(size)
        r.bold = bold

    para(NAME, size=13, bold=True, after=1)
    para(CONTACT, size=9.5, after=16)
    para(datetime.now(timezone.utc).strftime("%B %d, %Y"), size=10.5, after=16)
    para(f"Re: {title} at {company}", size=10.5, bold=True, after=12)
    para("Dear Hiring Team,", size=11, after=10)

    for block in text.split("\n\n"):
        block = block.strip()
        if block:
            para(block, size=11, after=10)

    para("Sincerely,", size=11, after=2)
    para(NAME, size=11, after=0)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
