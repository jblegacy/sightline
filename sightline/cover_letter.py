"""Cover letter generation — prose, not bullet selection, so this is a
different category from resume assembly (CLAUDE.md rule 1 is about resume
bullet text specifically). Same discipline as outreach drafts and the
answer workbench: grounded only in verified bullets and the posting's real
details, never inventing an achievement. Echoes the same bullets already
selected for this posting's resume so the two documents tell one story.

Three structural styles (STYLE_STRUCTURES) share all of that discipline and
differ only in shape — see generate_cover_letter_variants for the sandbox
that generates all three at once so the candidate can pick one before
anything is rendered or saved.
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

# Three structural shapes, reverse-engineered live from real recruiter-facing
# examples the candidate reviewed and liked (resumegenius.com's IT PM,
# nursing, and social-media letters). Everything except {structure} is
# shared discipline that doesn't change across styles: grounding, tone,
# gap-handling, voice. Only the shape of the pitch differs.
STYLE_STRUCTURES = {
    "traditional": """Structure, in this order — this is the most complete of the three formats, \
HARD CAP 320 words total:
1. Opening — the shared opener below. Nothing else in that sentence.
2. Context — ONE paragraph (2-3 sentences), prose only, no bullets yet: your most recent or most \
relevant work, framed by scope and responsibility.
3. Company-research beat — ONE sentence naming something specific and real about this company, \
pulled from the company signals or the JD itself — not generic enthusiasm ("I'd love to work \
here"), an actual detail (a market, a product line, something they're building or scaling). If \
nothing specific enough is available in what you're given, skip this sentence entirely rather \
than inventing one — a vague substitute is worse than no beat at all.
4. Transition sentence, then ONE line per JD category identified above, in the JD's own order — \
not a free pick of your best 2-4 wins. Each line connects your closest matching verified \
achievement to that category's actual ask (see "show your work" above). If a category has no \
strong direct match, use the closest honest, defensible connection rather than skipping it \
outright — e.g. documentation and playbook-building work is legitimate evidence for a "training" \
category even if the original bullet wasn't literally about training employees. Never fabricate \
a connection that isn't defensible, but look hard before deciding one doesn't exist. Each line \
starts with "- ", is one line, no sub-clauses, and stays under about 30 words.
5. Close — ONE sentence: a value statement plus an open door (offer to share more), not a repeat \
of what you just said.""",
    "compressed": """Structure, in this order — this is the shortest of the three formats, HARD \
CAP 160 words total, no padding. Length discipline is the entire point of this format, which \
means it does NOT attempt full category coverage — that's what Traditional and Warm are for:
1. Opening — the shared opener below.
2. ONE dense paragraph, prose only — NO bullet list anywhere in this version, and only ONE \
concrete example, not two. Ground that one example in whichever JD category got the most space \
or detail in the posting — the one a skimmed reading proves you actually noticed. Fold your \
single strongest quantified win directly into a sentence that also says why it matters for that \
category (see "show your work" above). Do not add a second anecdote, even a short one — that's \
what breaks the word cap.
3. ONE more sentence — one additional concrete strength or credibility marker, still prose, not \
its own multi-sentence paragraph.
4. Close — ONE sentence, forward-looking, and name how you can be reached even though it's \
already in the header — that inline contact line is what makes the shortest letters read as a \
direct note rather than a formatted document.
If a draft would run over 160 words, cut the credibility-marker sentence (step 3) before cutting \
anything else.""",
    "warm": """Structure, in this order — target 180-240 words total:
1. Opening — the shared opener below.
2. Transition sentence, then ONE line per JD category identified above, in the JD's own order — \
not a free pick of your best wins. Each line connects your closest matching verified achievement \
to that category's actual ask (see "show your work" above). If a category has no strong direct \
match, use the closest honest, defensible connection rather than skipping it outright. Each line \
starts with "- ", is one line, no sub-clauses, and stays under about 30 words.
3. Close — ONE to two sentences: confident and forward-looking, naming a concrete next step \
you'll personally take (e.g. following up by a specific point in time) rather than a vague \
"looking forward to hearing from you".""",
}

STYLE_LABELS = {
    "traditional": "Traditional",
    "compressed": "Direct",
    "warm": "Warm",
}

STYLE_DESCRIPTIONS = {
    "traditional": "Company-research beat + one line per JD category. Most formal, most complete.",
    "compressed": "Prose only, no bullets, one category only. Shortest — reads like a note.",
    "warm": "One line per JD category + a concrete follow-up commitment.",
}

STYLES = tuple(STYLE_STRUCTURES)

SYSTEM_PROMPT = """You write a cover letter for a job application, grounded ONLY in the \
candidate's verified resume bullets given below plus the real details of this posting. Never \
invent an achievement, number, or outcome that isn't in the verified bullets.

Recruiters scan hundreds of these — every sentence you'd cut from a first draft, cut.

Opening line, every style, no exceptions: "I'm excited to apply for the [Role] at [Company]" \
(this exact opener overrides VOICE RULES 6, 11, and 12 below — confirmed live as what the \
candidate wants, every time, not just as a fallback). No "ideal candidate," no restating what \
the role does back to the reader.

Find the JD's own top-level categories before writing anything. If it has literal section \
headers (e.g. "AI Adoption & Training" / "Business Process Analysis" / "Agent Strategy & \
Design" / "Adoption Measurement"), those ARE the categories, in that exact order — use the JD's \
own words for them, don't paraphrase or merge them. If it's a flat list with no headers, group \
the stated responsibilities into 3-5 clear thematic clusters yourself. This matters: postings in \
this space are very often structured exactly this way, and a letter that quietly cherry-picks \
2-3 easy wins while ignoring a named section reads as not having read past the first paragraph — \
the candidate has said this explicitly and it applies to every posting, not just this one.

Hit every category without announcing it. Never label a line with the category name ("On Agent \
Strategy and Design, I..." / "For Adoption Measurement, ..."). That reads like a worksheet, not a \
person, and the candidate has said directly it's too on-the-nose. The category should be obvious \
from what the line is actually about — the same way Business Process Analysis is unmistakable in \
"I mapped a client's inventory workflow end to end before ever picking a platform" without the \
words "business process analysis" appearing anywhere in the sentence. Weave the connection into \
natural language exactly like that; never scaffold it with a label.

Show your work. Don't just list what you did — say why it specifically matters for THIS role, \
and for the category it's answering. A fact alone ("built a finance system") is resume content; \
a fact plus its relevance ("built a finance system from scratch, which is the same kind of \
ground-up build this role's Business Process Analysis work needs") is what a cover letter adds \
that a resume can't. Every achievement you cite must connect explicitly to the category it's \
standing in for — pull the connection from the JD's own language, and don't assume the reader \
will draw the line themselves. Draw it for them, in the same sentence or line.

State each achievement in your own words, in a natural, conversational register — never a copy \
of the verified bullet's phrasing. The number, outcome, and claim must match a verified bullet \
exactly; the sentence construction must not. A line that reads like it was lifted straight off a \
resume is a bug — a cover letter is prose meant to be read like someone talking, not scanned in \
resume fragments. When a structure below calls for multiple lines, vary how each one starts: \
three or four in a row all opening with a past-tense verb ("Built... Created... Standardized...") \
is exactly the parallel cadence that makes a cover letter read like resume bullets pasted into a \
document instead of a person explaining their fit. Break the pattern — lead one line with the \
outcome, another with the relevance, another with the work itself.

Confident, not cocky. State what you did and let it speak for itself — never tell the reader \
what the role "actually" is, what they're "actually" asking for beneath the posting's own words, \
or frame yourself as seeing something about the job that they don't. Never compare yourself to \
other candidates ("further than most candidates," "unlike other applicants") — you have no idea \
who else applied, and the claim reads as arrogant rather than as evidence.

{structure}

Never name a gap, missing skill, or unmet requirement anywhere in the letter — this overrides \
VOICE RULE 7 below. A cover letter is a pitch, not a disclosure; gaps get discussed in the \
interview if they come up, not volunteered here. Pick bullets and framing that put the strongest \
foot forward and simply don't mention what isn't there.

No corporate throat-clearing, no restating the resume verbatim, no invented personality or \
passion claims, no sentence that could be pasted into any other cover letter unchanged. Return \
only the letter body — no salutation or signature, those are added separately. Separate each \
block above (opening+context, company-research beat, bullet list, close) with a blank line.

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


def _generate_styled(
    client: AnthropicClient,
    posting: dict[str, Any],
    score: dict[str, Any],
    bullets: list[dict[str, Any]],
    selected_bullet_refs: list[str],
    style: str,
    answers: list[dict[str, Any]] | None = None,
) -> tuple[str, float]:
    if style not in STYLE_STRUCTURES:
        raise ValueError(f"unknown cover letter style {style!r} — must be one of {STYLES}")
    verified = [b for b in bullets if b.get("status") == "verified"]
    selected = [b for b in verified if b["ref"] in selected_bullet_refs]
    other = [b for b in verified if b["ref"] not in selected_bullet_refs]
    system = SYSTEM_PROMPT.format(
        structure=STYLE_STRUCTURES[style],
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


def generate_cover_letter(
    client: AnthropicClient,
    posting: dict[str, Any],
    score: dict[str, Any],
    bullets: list[dict[str, Any]],
    selected_bullet_refs: list[str],
    answers: list[dict[str, Any]] | None = None,
    style: str = "warm",
) -> tuple[str, float]:
    return _generate_styled(client, posting, score, bullets, selected_bullet_refs, style, answers)


def generate_cover_letter_variants(
    client: AnthropicClient,
    posting: dict[str, Any],
    score: dict[str, Any],
    bullets: list[dict[str, Any]],
    selected_bullet_refs: list[str],
    answers: list[dict[str, Any]] | None = None,
    styles: tuple[str, ...] = STYLES,
) -> dict[str, tuple[str, float]]:
    """The sandbox — generates all requested styles from the same grounding
    data in one call. Text only, nothing rendered or saved; the caller
    decides which (if any) becomes the real document."""
    return {
        style: _generate_styled(client, posting, score, bullets, selected_bullet_refs, style, answers)
        for style in styles
    }


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
