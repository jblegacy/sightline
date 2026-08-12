"""Answer workbench — interactive drafting of application-question answers,
grounded in the verified bullet library and existing answers so a
back-and-forth chat can't invent facts the way an ungrounded session did for
the Convergent Research application. The chat itself is disposable scratch
work; only the final saved answer persists (see db.upsert_answer) — same
principle as assembly: selection/drafting from real material, not invention,
and the durable artifact is the reviewed output, not the process.

Every saved answer now keeps the literal question text it was asked as (not
just a question_type slug), and the library is fed back into every chat's
context in full — see _format_answers. With a few dozen rows this is
"keyword matching plus one model call," not a search problem; per CLAUDE.md
that's the right amount of machinery here, not embeddings or a vector store.
"""
from __future__ import annotations

import re
from typing import Any

from sightline.anthropic_client import AnthropicClient
from sightline.voice import VOICE_RULES, voice_reference

MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """You help draft answers to job-application questions, in the candidate's own \
voice, grounded ONLY in the facts given below — their verified resume bullets and any answers \
already in their library. Never invent a fact, number, or outcome that isn't in this material. \
If answering well needs a detail you don't have, ask the candidate for it rather than guessing \
or writing around it with vague language.

This is collaborative drafting, not one-shot generation — propose a draft, then revise based on \
what the candidate tells you. Match the voice of the existing answers below: specific, concrete, \
no corporate throat-clearing, no invented enthusiasm.

Before drafting anything, check the existing answer library below for a question that closely \
resembles the one just asked — same underlying question even if worded differently, not just a \
shared keyword. If you find one, say so explicitly at the start of your reply, name which entry \
it resembles (its ref and its question text), and ask whether to reuse it as-is, adapt it for \
this posting, or write fresh — never silently reuse an old answer without saying so, and never \
ignore a clear match and draft from scratch as if it were new.

{voice_rules}

VOICE REFERENCE (candidate's own writing):
{voice_reference}

VERIFIED RESUME BULLETS:
{bullets}

EXISTING ANSWER LIBRARY:
{answers}{posting_context}"""


def _format_bullets(bullets: list[dict[str, Any]]) -> str:
    verified = [b for b in bullets if b.get("status") == "verified"]
    if not verified:
        return "(none verified yet)"
    return "\n".join(f"- [{b['ref']}] {b['text']}" for b in verified)


def _format_answers(answers: list[dict[str, Any]]) -> str:
    usable = [a for a in answers if a.get("status") in ("ready", "verified")]
    if not usable:
        return "(none yet)"
    lines = []
    for a in usable:
        question_text = a.get("question_text")
        asked_as = f' — asked as: "{question_text}"' if question_text else ""
        lines.append(f"- [{a['ref']}] ({a['question_type']}){asked_as}: {a['text']}")
    return "\n".join(lines)


_SLUG_STRIP = re.compile(r"[^a-z0-9\s]")
_SLUG_SPACE = re.compile(r"\s+")


def slugify_question(question_text: str, existing_types: list[str] | None = None, max_words: int = 6) -> str:
    """Deterministic question_type suggestion from the literal question text
    - no model call needed for this, a plain slugify is the whole job. First
    N meaningful words, lowercased, snake_cased; a numeric suffix breaks a
    collision against question_types already in the library rather than
    silently merging two different questions under one slug."""
    cleaned = _SLUG_STRIP.sub("", question_text.lower())
    words = _SLUG_SPACE.sub(" ", cleaned).strip().split(" ")
    stopwords = {"a", "an", "the", "to", "you", "your", "me", "how", "what", "tell", "about", "of", "do"}
    words = [w for w in words if w and w not in stopwords][:max_words] or ["question"]
    base = "_".join(words)

    existing = set(existing_types or [])
    if base not in existing:
        return base
    n = 2
    while f"{base}_{n}" in existing:
        n += 1
    return f"{base}_{n}"


def build_system_prompt(
    bullets: list[dict[str, Any]],
    answers: list[dict[str, Any]],
    posting: dict[str, Any] | None,
) -> str:
    posting_context = ""
    if posting:
        company = (posting.get("companies") or {}).get("name", "the company")
        jd_excerpt = (posting.get("jd_text") or "")[:1500]
        posting_context = (
            f"\n\nCURRENT APPLICATION CONTEXT:\nRole: {posting.get('title')} at {company}"
            f"\nJD excerpt: {jd_excerpt}"
        )
    return SYSTEM_PROMPT.format(
        voice_rules=VOICE_RULES, voice_reference=voice_reference(answers),
        bullets=_format_bullets(bullets), answers=_format_answers(answers),
        posting_context=posting_context,
    )


def chat_reply(
    client: AnthropicClient,
    bullets: list[dict[str, Any]],
    answers: list[dict[str, Any]],
    posting: dict[str, Any] | None,
    messages: list[dict[str, str]],
) -> tuple[str, float]:
    system = build_system_prompt(bullets, answers, posting)
    return client.chat_call(model=MODEL, system=system, messages=messages)


def next_ref(answers: list[dict[str, Any]]) -> str:
    """New answers keep extending the existing 'A' namespace (A1..A11
    already exist) rather than starting a new prefix."""
    nums = [int(a["ref"][1:]) for a in answers if a["ref"][0] == "A" and a["ref"][1:].isdigit()]
    return f"A{max(nums, default=0) + 1}"
