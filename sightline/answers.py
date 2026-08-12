"""Answer workbench — interactive drafting of application-question answers,
grounded in the verified bullet library and existing answers so a
back-and-forth chat can't invent facts the way an ungrounded session did for
the Convergent Research application. The chat itself is disposable scratch
work; only the final saved answer persists (see db.upsert_answer) — same
principle as assembly: selection/drafting from real material, not invention,
and the durable artifact is the reviewed output, not the process.
"""
from __future__ import annotations

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
    return "\n".join(f"- [{a['ref']}] ({a['question_type']}): {a['text']}" for a in usable)


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
