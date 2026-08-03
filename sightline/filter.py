"""Stage 2 — deterministic filter. No model calls, can't hallucinate.

Archives only. Comp and location-restriction judgment calls live elsewhere
(comp is a queue-layer flag per CLAUDE.md; location restrictions get flagged
during scoring, since detecting them needs reading the JD, not a keyword
list). See docs/SIGHTLINE_BUILD_SPEC_V2.md §5 for the full reasoning.
"""
from __future__ import annotations

from typing import Any


def apply_filter(posting: dict[str, Any], red_flag_phrases: list[str]) -> tuple[str, str | None]:
    """Returns (status, filter_reason). status is 'archived' or 'filtered'
    ('filtered' meaning: passed this stage, ready for scoring)."""
    if posting.get("remote_flag") == "false":
        return "archived", "not remote"

    if red_flag_phrases:
        jd_text = (posting.get("jd_text") or "").lower()
        for phrase in red_flag_phrases:
            if phrase.lower() in jd_text:
                return "archived", f"red-flag phrase: {phrase}"

    return "filtered", None
