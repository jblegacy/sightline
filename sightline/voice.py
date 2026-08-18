"""Shared voice grounding for every place Sightline generates freeform prose
in the candidate's own name — cover letters, outreach drafts. Resume bullets
are selected verbatim from the library and never need this; voice only
matters where generation, not selection, is the point.
"""
from __future__ import annotations

from typing import Any

# Derived from James Beam's Job Application Writing Guide (provided
# 2026-08-17), which supersedes the earlier VOICE_PROFILE.md-derived rules —
# per that guide's own instruction, its four confirmed real cover letters
# (CodePath, Mercury, Argano, Whip Around) are the strongest source of truth
# for voice, studied and not to be exceeded in polish.
VOICE_RULES = """VOICE RULES — apply these exactly; they come from the candidate's own writing \
guide and confirmed real letters, not a generic style guide:

1. Use spaced hyphens " - " as connectors, the way a comma or dash would normally be used. NEVER \
use an em dash (—) or en dash (–) anywhere. NEVER use a colon (:) either, including to introduce \
a clause, a list, or an explanation — use a period or a spaced hyphen instead.
2. Vary sentence length naturally rather than producing uniform mid-length sentences — that \
uniformity is the most common tell of generated prose. A little natural imperfection is good; \
not every sentence needs to sound perfectly engineered.
3. Banned words and phrases, no exceptions: intersection, at the intersection of, synergy, \
deeply passionate, I am uniquely positioned, I believe my background makes me an ideal candidate, \
spearheaded, orchestrated, thrilled, delve, tapestry, testament, robust (as filler), holistic, \
best-in-class, value-add, circle back, moreover, furthermore, in addition, "I am writing to," \
"I would be thrilled." Avoid "leverage" (as a verb) unless there is genuinely no better word.
4. No generic corporate language and no obvious AI buzzwords — if a sentence could be pasted into \
any other cover letter unchanged, cut it.
5. Never mirror the JD's own phrasing back at it. Translate the underlying ask into plain language \
instead — if a posting says "build a context layer for AI," don't write "I have experience \
building context layers for AI," explain the actual capability: "I've built systems that take \
information spread across different workflows and turn it into something people can actually use."
6. Don't overuse the names of software tools or platforms just to prove technical knowledge. What \
matters is what he built, why he built it, how it worked, and what changed because of it — not an \
inventory of tools.
7. "We" for team/company work, "I" only for what he personally built.
8. Never explain the company's own mission back to them.
9. Confident, not cocky — state what was done and let it speak for itself. Never tell the reader \
what the role "actually" is beneath its own words, and never compare against other candidates \
("further than most applicants" and similar) — there's no way to know who else applied, and the \
claim reads as arrogant rather than as evidence.
10. Never use conditional language that hands the reader an easy no — "if you think I'm a fit," \
"if you'd like to take a look," "if you're interested," "I'd be happy to chat if..." State the \
action directly instead.
11. Never use markdown formatting in running prose — no literal **asterisks** or # headers \
surviving into the text. A real bulleted list (e.g. the AI-projects block in a cover letter) is \
fine and expected; stray markdown syntax leaking into plain prose is not. One narrow, explicit \
exception: an AI-project's name at the very start of its bullet line may use **Name** markdown, \
matching the confirmed real letters' own convention — the renderer turns that into an actual bold \
run, never literal asterisks in the delivered document. Nowhere else.

Tone throughout: casual but professional, direct, confident without being arrogant, \
conversational, specific, human, shorter rather than wordier.

Core positioning — the throughline for everything written in his name: James understands how \
businesses actually work and has started applying AI directly to those workflows. That is a \
meaningfully different, stronger claim than "James knows AI," and writing should never flatten it \
down to the weaker version. The recurring pattern behind his best work: understand the business \
problem first, then decide whether the right answer is a process change, a third-party tool, \
automation, AI, or something that needs to be built from scratch — not AI reached for by default.

Calibration — a generic, wrong-register version, then the same idea in his actual confirmed voice \
(from the real, sent Argano cover letter):

WRONG (generic — do not produce anything resembling this):
"I am writing to express my deep passion for this opportunity. With a proven track record of \
leveraging AI to drive synergy across cross-functional teams, I believe my background makes me an \
ideal candidate for this role."

RIGHT (his actual voice — this is the register to match):
"What I enjoy most is sitting between the problem and the technology. Understand what the \
business is trying to accomplish, break the problem down, determine where AI or automation can \
create real leverage, and then work with the technical team to turn that into something that can \
actually be deployed and adopted."

A second real example, from the confirmed Mercury letter, showing how he explains a capability \
without naming it as a category:
"What I've learned from building these is that the hard part is rarely getting a first version to \
work. The harder part is deciding what information matters, how it should be structured, where \
people still need to make the call, and how to keep the system reliable as the underlying tools \
and information change.\""""


def voice_reference(answers: list[dict[str, Any]], n: int = 3) -> str:
    """The bullet library is terse resume fragments — it can't teach voice.
    The answer library has actual multi-sentence prose the candidate wrote
    and personally confirmed. Longest usable entries make the richest style
    samples. See COVER_LETTER_EXAMPLES below for cover-letter-specific
    reference — this function backs the more general answer workbench."""
    usable = [a for a in answers if a.get("status") in ("ready", "verified") and len(a.get("text") or "") > 200]
    usable.sort(key=lambda a: -len(a["text"]))
    if not usable:
        return "(none available)"
    return "\n\n".join(f"[{a['question_type']}]\n{a['text']}" for a in usable[:n])


# Two of the four confirmed, actually-sent cover letters (2026-08-17), given
# in full — not excerpted — per the writing guide's own instruction that
# these are the strongest source of truth for voice and structure. CodePath
# and Whip Around are the other two; Mercury and Argano were picked here for
# range (a personal-connection opener vs. a consulting-flavored one) without
# making the reference block too long to be useful in a system prompt.
COVER_LETTER_EXAMPLES = """CONFIRMED REAL EXAMPLE 1 (Mercury — personal-connection opener):

I'm excited to apply for the AI Context Operations Lead role at Mercury. I've been a Mercury \
customer across multiple small businesses and have genuinely enjoyed using the product, so the \
opportunity to help build the systems behind the company is especially exciting to me.

The role also lines up closely with the work I've been doing around operations, AI, and workflow \
design. I enjoy taking information that is spread across different systems and turning it into \
something people can actually use, whether that means a better process, a structured system, an \
automation, or an AI workflow.

A few examples of the AI systems I've built recently

- **Sightline** Built and launched an AI powered workflow system in 48 hours that handles job \
discovery, qualification, document generation, application tracking, outreach, and follow ups \
while keeping human review in the loop. It now surfaces roughly 50 roles a day and reduced a two \
hour daily search process to a morning review.
- **Barometer** Built and tested an automated prediction market system using live market and weather \
APIs and a 540 profile testing matrix across hundreds of model configurations. The testing showed \
that market speed and weather data latency removed the trading edge, so I paused the system \
rather than continuing to automate something that did not make economic sense.
- **Ottonimus** Built a functional AI powered SEO and GEO platform prototype that brought website \
performance, technical SEO, backlinks, local SEO, Google presence, and emerging GEO workflows \
into one system.

What I've learned from building these is that the hard part is rarely getting a first version to \
work. The harder part is deciding what information matters, how it should be structured, where \
people still need to make the call, and how to keep the system reliable as the underlying tools \
and information change. That is the part of this work I enjoy most.

My broader operating background has given me a similar perspective. At Comarkco, I helped build \
the infrastructure behind a CPG business that grew from $250K to $12M in 12 months. I worked with \
teams to understand how information and work moved through the business, found gaps and \
bottlenecks, and built systems that gave the organization better visibility and control.

What makes Mercury particularly interesting to me is that this role is solving that same kind of \
problem at a much larger scale. A trusted context layer can make a huge difference in how quickly \
people find information, how confidently leaders make decisions, and how effectively AI systems \
can work across an organization. I'd be excited to help Mercury build that foundation while being \
part of a company whose product I already know and value.

I'd love the opportunity to bring my operating experience, systems thinking, and hands on AI work \
to Mercury and help build the next stage of its internal AI infrastructure.

CONFIRMED REAL EXAMPLE 2 (Argano — consulting-flavored, no AI-projects bullet used for the close):

I'm excited to apply for the Senior Principal Consultant, Agentic Operations role. The opportunity \
stood out to me because it brings together a lot of the work I've spent my career doing: solving \
complex operational problems, building the business case for change, working across technical and \
business teams, and increasingly using AI to rethink how work gets done.

At Comarkco, I helped build the operating infrastructure behind a CPG business that grew from \
$250K to $12M in 12 months. I worked across supply chain, inventory, finance, technology, and \
operations, often starting with a messy business problem and working with the team to understand \
the process end to end. From there, I helped determine what needed to change, whether the answer \
was a new process, a third party system, additional resources, or technology we needed to build \
ourselves.

That experience has shaped how I approach transformation. I'm less interested in technology for \
its own sake and more interested in understanding the business outcome first, then figuring out \
what will actually move the needle. I've built business cases, managed complex initiatives, \
worked across internal and external technical teams, and helped translate between what the \
business needed and what technology could realistically deliver.

More recently, I've been applying that same approach to AI. I've built and shipped several AI \
systems around real workflows:

- **Sightline** An AI powered workflow system that handles job discovery, qualification, document \
generation, application tracking, outreach, and follow ups while keeping human judgment in the \
loop.
- **Barometer** An automated prediction market system that I tested against live market and weather \
data before determining that the underlying latency made the opportunity uneconomic and pausing \
the project.
- **Ottonimus** An AI powered SEO and GEO platform prototype that brought multiple technical and \
marketing workflows into a single system.

What I enjoy most is sitting between the problem and the technology. Understand what the business \
is trying to accomplish, break the problem down, determine where AI or automation can create real \
leverage, and then work with the technical team to turn that into something that can actually be \
deployed and adopted.

Argano's focus on high performance operations and the role's emphasis on connecting business \
strategy with AI and technical execution is a particularly strong match for how I like to work. \
I'd be excited to bring my operating experience, CPG background, financial and commercial \
perspective, and hands on AI experience to help clients turn transformation opportunities into \
measurable business results."""
