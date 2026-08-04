import httpx
import pytest
import respx

from sightline.theirstack import (
    CreditsExhausted,
    TheirStackClient,
    TheirStackError,
    build_filters_for_profile,
    verify_webhook_signature,
)

BASE = "https://api.theirstack.com"


# ---- signature verification: official test vector from
# https://theirstack.com/en/docs/webhooks/verify-webhook-signatures ----


def test_verify_webhook_signature_official_vector():
    secret = "It's a Secret to Everybody"
    payload = b"Hello, World!"
    expected = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"
    assert verify_webhook_signature(payload, secret, expected) is True


def test_verify_webhook_signature_rejects_wrong_secret():
    payload = b"Hello, World!"
    expected = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"
    assert verify_webhook_signature(payload, "wrong secret", expected) is False


def test_verify_webhook_signature_rejects_tampered_payload():
    secret = "It's a Secret to Everybody"
    expected = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"
    assert verify_webhook_signature(b"Hello, World?", secret, expected) is False


def test_verify_webhook_signature_missing_header():
    assert verify_webhook_signature(b"anything", "secret", None) is False


# ---- required-filter guard ----


def test_search_jobs_rejects_missing_required_filter():
    client = TheirStackClient(api_key="fake")
    with pytest.raises(TheirStackError, match="posted_at_max_age_days"):
        client.search_jobs({"remote": True})  # no posted_at_*/company_*_or filter


def test_free_count_rejects_missing_required_filter():
    client = TheirStackClient(api_key="fake")
    with pytest.raises(TheirStackError):
        client.free_count({"remote": True})


# ---- profile + settings -> filters mapping ----


def test_build_filters_for_profile_no_salary_filter():
    profile = {"title_include": ["workflow automation"], "title_exclude": []}
    filters = build_filters_for_profile(profile, {})
    assert "min_salary_usd" not in filters
    assert "max_salary_usd" not in filters


def test_build_filters_for_profile_uses_profile_title_lists_not_settings():
    profile = {"title_include": ["workflow automation"], "title_exclude": ["ai engineer"]}
    filters = build_filters_for_profile(profile, {})
    assert filters["job_title_or"] == ["workflow automation"]
    assert filters["job_title_not"] == ["ai engineer"]


def test_build_filters_for_profile_open_only_true_by_default():
    profile = {"title_include": ["workflow automation"]}
    filters = build_filters_for_profile(profile, {})
    assert filters["is_closed"] is False


def test_build_filters_for_profile_open_only_false_omits_is_closed():
    profile = {"title_include": ["workflow automation"]}
    filters = build_filters_for_profile(profile, {"open_only": False})
    assert "is_closed" not in filters


def test_build_filters_for_profile_satisfies_required_filter_guard():
    from sightline.theirstack import _require_dedup_filter

    profile = {"title_include": ["workflow automation"]}
    filters = build_filters_for_profile(profile, {})
    _require_dedup_filter(filters)  # should not raise


def test_build_filters_for_profile_seniority_from_settings_included_when_set():
    profile = {"title_include": ["workflow automation"]}
    filters = build_filters_for_profile(profile, {"seniority": ["senior", "staff"]})
    assert filters["job_seniority_or"] == ["senior", "staff"]


def test_build_filters_for_profile_seniority_omitted_when_empty():
    profile = {"title_include": ["workflow automation"]}
    filters = build_filters_for_profile(profile, {"seniority": []})
    assert "job_seniority_or" not in filters


def test_build_filters_for_profile_source_exclude_from_settings_included_when_set():
    profile = {"title_include": ["workflow automation"]}
    filters = build_filters_for_profile(profile, {"source_exclude": ["linkedin.com"]})
    assert filters["url_domain_not"] == ["linkedin.com"]


def test_build_filters_for_profile_salary_filter_off_by_default_even_with_values_set():
    profile = {"title_include": ["workflow automation"]}
    settings = {"fetch_salary_min": 90000, "fetch_salary_max": 200000}
    filters = build_filters_for_profile(profile, settings)
    assert "min_salary_usd" not in filters
    assert "max_salary_usd" not in filters


def test_build_filters_for_profile_salary_filter_applied_when_explicitly_enabled():
    profile = {"title_include": ["workflow automation"]}
    settings = {"fetch_salary_filter": True, "fetch_salary_min": 90000, "fetch_salary_max": 200000}
    filters = build_filters_for_profile(profile, settings)
    assert filters["min_salary_usd"] == 90000
    assert filters["max_salary_usd"] == 200000


# ---- HTTP layer, mocked with respx ----


@respx.mock
def test_free_count_hits_search_with_free_params_and_parses_total():
    route = respx.post(f"{BASE}/v1/jobs/search").mock(
        return_value=httpx.Response(200, json={"data": [], "metadata": {"total_results": 2312}})
    )
    client = TheirStackClient(api_key="fake")
    total = client.free_count({"posted_at_max_age_days": 30})
    assert total == 2312
    sent = route.calls.last.request
    import json as _json

    body = _json.loads(sent.content)
    assert body["blur_company_data"] is True
    assert body["include_total_results"] is True
    assert body["limit"] == 1


@respx.mock
def test_search_jobs_raises_on_402_credits_exhausted():
    respx.post(f"{BASE}/v1/jobs/search").mock(
        return_value=httpx.Response(402, json={"error": {"code": "credits_exhausted"}})
    )
    client = TheirStackClient(api_key="fake")
    with pytest.raises(CreditsExhausted):
        client.search_jobs({"posted_at_max_age_days": 30})


@respx.mock
def test_upsert_saved_search_creates_when_none_exists():
    respx.get(f"{BASE}/v0/saved_searches").mock(return_value=httpx.Response(200, json=[]))
    create = respx.post(f"{BASE}/v0/saved_searches").mock(
        return_value=httpx.Response(201, json={"id": 42, "name": "sightline"})
    )
    client = TheirStackClient(api_key="fake")
    result = client.upsert_saved_search("sightline", {"posted_at_max_age_days": 30})
    assert result["id"] == 42
    assert create.called
    import json as _json

    body = _json.loads(create.calls.last.request.content)
    # SavedSearchCreate uses `body`/`type`, not `filters`/`search_type` —
    # verified against the live OpenAPI spec, easy field to get wrong.
    assert body["body"] == {"posted_at_max_age_days": 30}
    assert body["type"] == "jobs"


@respx.mock
def test_upsert_saved_search_patches_when_existing():
    respx.get(f"{BASE}/v0/saved_searches").mock(
        return_value=httpx.Response(200, json=[{"id": 42, "name": "sightline"}])
    )
    patch = respx.patch(f"{BASE}/v0/saved_searches/42").mock(
        return_value=httpx.Response(200, json={"id": 42, "name": "sightline"})
    )
    client = TheirStackClient(api_key="fake")
    client.upsert_saved_search("sightline", {"posted_at_max_age_days": 30})
    assert patch.called


@respx.mock
def test_upsert_webhook_creates_with_signing_secret():
    respx.get(f"{BASE}/v0/webhooks").mock(return_value=httpx.Response(200, json=[]))
    create = respx.post(f"{BASE}/v0/webhooks").mock(
        return_value=httpx.Response(201, json={"id": 7})
    )
    client = TheirStackClient(api_key="fake")
    client.upsert_webhook(
        saved_search_id=42,
        url="https://example.com/webhooks/theirstack",
        secret="a-long-enough-secret-value",
        event_types=["job_new", "job_closed"],
    )
    import json as _json

    body = _json.loads(create.calls.last.request.content)
    # WebhookCreateRequestV0 uses `search_id`/`active_event_types`, not
    # `saved_search_id`/`event_types` — and has no `name` field at all.
    assert body["search_id"] == 42
    assert body["active_event_types"] == ["job_new", "job_closed"]
    assert body["secret"] == "a-long-enough-secret-value"
    assert body["trigger_once_per_company"] is False


@respx.mock
def test_upsert_webhook_listen_from_now_sets_listening_start_time():
    respx.get(f"{BASE}/v0/webhooks").mock(return_value=httpx.Response(200, json=[]))
    create = respx.post(f"{BASE}/v0/webhooks").mock(return_value=httpx.Response(201, json={"id": 7}))
    client = TheirStackClient(api_key="fake")
    client.upsert_webhook(
        saved_search_id=42,
        url="https://example.com/webhooks/theirstack",
        secret="a-long-enough-secret-value",
        event_types=["job_new"],
        listen_from_now=True,
    )
    import json as _json

    body = _json.loads(create.calls.last.request.content)
    assert "listening_start_time" in body  # omitted/null would replay the full backlog


@respx.mock
def test_find_webhook_for_search_matches_by_search_id():
    respx.get(f"{BASE}/v0/webhooks").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 1, "url": "https://example.com/webhooks/theirstack", "search_id": 42},
                {"id": 2, "url": "https://example.com/webhooks/theirstack", "search_id": 43},
            ],
        )
    )
    client = TheirStackClient(api_key="fake")
    found = client.find_webhook_for_search(43)
    assert found is not None
    assert found["id"] == 2
