"""Shared voice grounding for every place Sightline generates freeform prose
in the candidate's own name — cover letters, outreach drafts. Resume bullets
are selected verbatim from the library and never need this; voice only
matters where generation, not selection, is the point.
"""
from __future__ import annotations

from typing import Any

# Derived from analysis of the candidate's own unedited writing (VOICE_PROFILE.md,
# provided 2026-08-05). Condensed rules + a verbatim calibration pair, per that
# document's own instruction: examples are verbatim, not paraphrased — the specific
# wording is the signal.
VOICE_RULES = """VOICE RULES — apply these exactly; they come from analysis of the candidate's \
own unedited writing, not a generic style guide:

1. Use spaced hyphens " - " as connectors, the way a comma, colon, or dash would normally be \
used. NEVER use an em dash (—) or en dash (–) anywhere — this is the single fastest tell that \
text isn't his.
2. Vary sentence length aggressively: long accumulating sentences that keep stacking with \
"and"/"but"/" - ", then a short flat sentence for contrast. Never produce uniform mid-length \
sentences — that's the most common tell of generated prose. (In a format too short for this \
rhythm to fit — a subject line, a LinkedIn connection note — prioritize the other rules instead.)
3. At most one dry, understated parenthetical aside in the whole piece — never a joke, a \
self-deprecating or scope-narrowing one.
4. Build chronologically within a paragraph: situation, then problem, then action, then result. \
Never lead with the conclusion — let the point emerge from the facts.
5. Concrete nouns over abstractions — name the actual thing, not its category.
6. Banned words and phrases: leverage (as a verb), synergy, spearheaded, orchestrated, \
passionate about, thrilled, delve, tapestry, testament, robust (as filler), holistic, \
best-in-class, value-add, circle back, moreover, furthermore, in addition, "I am writing to," \
"I am excited to," "I would be thrilled."
7. Admit a real limit somewhere if one exists — stated flatly, no apology, no softening.
8. "We" for team/company work, "I" only for what he personally built.
9. Never explain the company's own mission back to them.
10. Never mirror the posting's distinctive or unusual phrasing back at it — generic industry \
terms are fine, but echoing their specific constructions reads as machine-generated.
11. Never open with "I am writing to apply" (or, for outreach, "I noticed you work at X") or any \
conclusion-first construction — start with a fact or a situation.
12. Motivation, if it appears, reads as interest and curiosity ("I'm curious," "it would be neat \
to") — never as passion or excitement.

Calibration pair — the wrong register, then the same content in his actual voice:

WRONG (generic — do not produce anything resembling this):
"I am excited to apply for the Program Operations Manager position at Convergent Research. With \
over a decade of experience spearheading operational transformations, I am passionate about \
leveraging my expertise to drive impact in the scientific research space."

RIGHT (his actual voice — this is the register to match):
"I applied for the Program Operations Manager role and wanted to give you some context on where \
I'm coming from. Most of my background is building operational systems where none existed - most \
recently inventory tracking for a CPG client who had no idea where their units were, after a \
stockout cost them somewhere between $100K and $150K. Before that I spent five years in a \
university engineering department administering research grants, which is where the interest in \
this kind of work started.\""""


def voice_reference(answers: list[dict[str, Any]], n: int = 3) -> str:
    """The bullet library is terse resume fragments — it can't teach voice.
    The answer library has actual multi-sentence prose the candidate wrote
    and personally confirmed (the Convergent Research application answers
    among them). Longest usable entries make the richest style samples."""
    usable = [a for a in answers if a.get("status") in ("ready", "verified") and len(a.get("text") or "") > 200]
    usable.sort(key=lambda a: -len(a["text"]))
    if not usable:
        return "(none available)"
    return "\n\n".join(f"[{a['question_type']}]\n{a['text']}" for a in usable[:n])
