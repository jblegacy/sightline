"""Parses a job-posting URL into the manual-add form fields (title, company,
location, remote, jd_text) via one Haiku structured-output call over the
page's visible text — not a per-ATS-platform scraper, and not a new
discovery channel. TheirStack still owns bulk discovery (CLAUDE.md: "do not
build ATS scrapers, this layer is bought"); this is a single, user-initiated
fetch of one URL the candidate already found and chose to add by hand — the
same manual-add action that previously required pasting the JD text in too,
just with that one step automated.

CLAUDE.md rule 6: respects robots.txt and sends an identifying User-Agent.
"""
from __future__ import annotations

import urllib.robotparser
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from sightline.anthropic_client import AnthropicClient

MODEL = "claude-haiku-4-5"
USER_AGENT = (
    "SightlineBot/1.0 (+https://github.com/jblegacy/sightline; "
    "personal job-search tool, single on-demand fetch; contact: james@beamlegacy.com)"
)


class RobotsDisallowedError(Exception):
    pass


class _TextExtractor(HTMLParser):
    """Strips tags and drops script/style content — good enough for a model
    to read the actual posting, not a real renderer. Deliberately stdlib
    only: the extracted text goes straight to Haiku for the real parsing,
    so this only needs to be readable, not a faithful DOM reconstruction."""

    _SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self.chunks.append(text)


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return "\n".join(parser.chunks)


def _check_robots_allowed(url: str) -> None:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    try:
        resp = httpx.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=10.0, follow_redirects=True)
        if resp.status_code >= 400:
            return  # no robots.txt (or unreadable) — nothing there disallows us
        rp.parse(resp.text.splitlines())
    except httpx.HTTPError:
        return  # can't reach robots.txt at all — fail open rather than block a single user-initiated fetch
    if not rp.can_fetch(USER_AGENT, url):
        raise RobotsDisallowedError(f"{parsed.netloc}'s robots.txt disallows fetching this page")


EXTRACT_SYSTEM_PROMPT = """You extract job posting fields from raw page text pulled from a job \
board or ATS site (Greenhouse, Ashby, Workday, Oracle HCM, LinkedIn, a company's own careers \
page, etc.). The text may include navigation, cookie banners, and other page chrome around the \
actual posting — ignore that, extract only the real posting content.

If a field genuinely isn't present in the text, return an empty string for it (or "unclear" for \
remote) rather than guessing or inventing a value — an empty field the candidate fills in by hand \
is fine; a wrong guess they don't notice isn't.

`jd_text` should be the full job description as it actually reads — responsibilities, \
requirements, about-the-company, benefits if present — not a summary or a paraphrase. Preserve \
real section breaks where you can tell they existed."""

EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "company": {"type": "string"},
        "location": {"type": "string"},
        "remote": {"type": "string", "enum": ["true", "false", "unclear"]},
        "jd_text": {"type": "string"},
    },
    "required": ["title", "company", "location", "remote", "jd_text"],
}


def parse_posting_url(client: AnthropicClient, url: str) -> tuple[dict[str, Any], float]:
    """Fetches url, strips it to text, and asks Haiku to pull out the
    manual-add form fields. Raises RobotsDisallowedError if the site's
    robots.txt says no; httpx.HTTPError if the fetch itself fails."""
    _check_robots_allowed(url)
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=20.0, follow_redirects=True)
    resp.raise_for_status()
    # Bounded, not because a JD is ever this long, but because a page's
    # nav/footer/script chrome can otherwise pad the extracted text well
    # past what's useful — plenty of room left for a real posting either way.
    text = _html_to_text(resp.text)[:15000]

    result, cost_usd = client.structured_call(
        model=MODEL,
        system=EXTRACT_SYSTEM_PROMPT,
        user_content=f"URL: {url}\n\nPAGE TEXT:\n{text}",
        tool_name="submit_posting_fields",
        tool_description="Submit the extracted job posting fields.",
        input_schema=EXTRACT_SCHEMA,
        max_tokens=3000,
    )
    remote = {"true": True, "false": False}.get(result.get("remote"))  # None for "unclear"/anything else
    return {
        "title": result.get("title") or "",
        "company": result.get("company") or "",
        "location": result.get("location") or "",
        "remote": remote,
        "jd_text": result.get("jd_text") or "",
    }, cost_usd
