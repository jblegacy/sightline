from unittest.mock import MagicMock

import httpx
import pytest
import respx

from sightline.posting_parser import (
    RobotsDisallowedError,
    USER_AGENT,
    _check_robots_allowed,
    _html_to_text,
    parse_posting_url,
)

URL = "https://example.com/careers/jobs/123"


def test_html_to_text_strips_tags_and_script_content():
    html = "<html><head><style>.x{color:red}</style></head><body>" \
           "<script>var x=1;</script><h1>AI Enablement Lead</h1><p>Build AI workflows.</p></body></html>"
    text = _html_to_text(html)
    assert "AI Enablement Lead" in text
    assert "Build AI workflows." in text
    assert "color:red" not in text
    assert "var x=1" not in text


@respx.mock
def test_check_robots_allowed_permits_when_no_disallow_matches():
    respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /")
    )
    _check_robots_allowed(URL)  # doesn't raise


@respx.mock
def test_check_robots_allowed_raises_when_disallowed():
    respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /careers/")
    )
    with pytest.raises(RobotsDisallowedError):
        _check_robots_allowed(URL)


@respx.mock
def test_check_robots_allowed_fails_open_on_missing_robots_txt():
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(404))
    _check_robots_allowed(URL)  # no robots.txt at all — nothing disallows us


@respx.mock
def test_check_robots_allowed_fails_open_on_network_error():
    respx.get("https://example.com/robots.txt").mock(side_effect=httpx.ConnectError("boom"))
    _check_robots_allowed(URL)  # can't reach robots.txt — a single user fetch shouldn't block on that


@respx.mock
def test_parse_posting_url_happy_path():
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(URL).mock(
        return_value=httpx.Response(200, text="<html><body><h1>AI Enablement Lead</h1></body></html>")
    )
    fake_anthropic = MagicMock()
    fake_anthropic.structured_call.return_value = (
        {
            "title": "AI Enablement Lead",
            "company": "Acme Inc",
            "location": "Remote, US",
            "remote": "true",
            "jd_text": "Own AI adoption across the org.",
        },
        0.0012,
    )

    fields, cost_usd = parse_posting_url(fake_anthropic, URL)

    assert fields["title"] == "AI Enablement Lead"
    assert fields["company"] == "Acme Inc"
    assert fields["location"] == "Remote, US"
    assert fields["remote"] is True
    assert fields["jd_text"] == "Own AI adoption across the org."
    assert cost_usd == 0.0012
    # The fetched page text, not just the URL, reached the model.
    sent = fake_anthropic.structured_call.call_args.kwargs["user_content"]
    assert "AI Enablement Lead" in sent
    assert USER_AGENT in respx.calls.last.request.headers.get("user-agent", "")


@respx.mock
def test_parse_posting_url_maps_unclear_remote_to_none():
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(URL).mock(return_value=httpx.Response(200, text="<html><body>a job</body></html>"))
    fake_anthropic = MagicMock()
    fake_anthropic.structured_call.return_value = (
        {"title": "X", "company": "Y", "location": "", "remote": "unclear", "jd_text": "..."}, 0.001,
    )
    fields, _ = parse_posting_url(fake_anthropic, URL)
    assert fields["remote"] is None


@respx.mock
def test_parse_posting_url_raises_on_disallowed_robots():
    respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /")
    )
    with pytest.raises(RobotsDisallowedError):
        parse_posting_url(MagicMock(), URL)


@respx.mock
def test_parse_posting_url_raises_on_fetch_failure():
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(URL).mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        parse_posting_url(MagicMock(), URL)
