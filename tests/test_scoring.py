from unittest.mock import MagicMock

from sightline.scoring import DIMENSIONS, build_user_content, score_posting

SAMPLE_POSTING = {
    "title": "AI Automation Engineer",
    "location_raw": "US Remote",
    "remote_flag": "true",
    "comp_min": 140000,
    "comp_max": 170000,
    "posted_at": "2026-08-01",
    "jd_text": "Build internal AI automation tools. 5+ years required.",
}

SAMPLE_BULLETS = [
    {"ref": "BL-014", "text": "Architected an automation platform.", "tags": ["automation"], "variants": ["engineer"]},
]

FAKE_MODEL_RESULT = {
    "dimensions": {
        "role_fit": 20, "evidence_overlap": 15, "seniority_scope": 10,
        "remote_authenticity": 12, "comp_signal": 13, "company_stage_fit": 8, "red_flags": 0,
    },
    "total": 78,
    "rationale": "Strong overlap on automation work.",
    "keywords": ["AI automation", "internal tools"],
    "matched_bullet_refs": ["BL-014"],
    "unmet_requirements": ["Named enterprise stack experience"],
    "knockouts": ["5+ years required"],
    "coding_interview_signals": [],
    "suggested_variant": "engineer",
    "reports_to": "VP Engineering",
    "named_contacts": [],
    "target_titles": ["VP Engineering"],
    "company_signals": ["Recently raised Series B"],
}


def test_build_user_content_includes_posting_and_bullets():
    content = build_user_content(SAMPLE_POSTING, SAMPLE_BULLETS)
    assert "AI Automation Engineer" in content
    assert "BL-014" in content
    assert "$140000" in content


def test_build_user_content_handles_no_salary():
    posting = {**SAMPLE_POSTING, "comp_min": None, "comp_max": None}
    content = build_user_content(posting, SAMPLE_BULLETS)
    assert "not posted" in content


def test_dimensions_sum_matches_prototype_rubric():
    # DIMS in prototype/sightline-dashboard.html: 25+20+15+15+15+10-30 = 70
    assert sum(max_pts for _, max_pts in DIMENSIONS) == 70


def test_score_posting_maps_model_output_to_scores_row():
    fake_client = MagicMock()
    fake_client.structured_call.return_value = (FAKE_MODEL_RESULT, 0.00123)

    row = score_posting(fake_client, SAMPLE_POSTING, SAMPLE_BULLETS)

    assert row["total"] == 78
    assert row["rubric_version"] == "v1"
    assert row["model"] == "claude-haiku-4-5"
    assert row["matched_bullet_ids"] == ["BL-014"]
    assert row["knockouts"] == ["5+ years required"]
    assert row["unmet_requirements"] == ["Named enterprise stack experience"]
    assert row["reports_to"] == "VP Engineering"
    assert row["cost_usd"] == 0.00123
    assert row["coding_interview_signals"] == []


def test_score_posting_empty_reports_to_becomes_none():
    result = {**FAKE_MODEL_RESULT, "reports_to": ""}
    fake_client = MagicMock()
    fake_client.structured_call.return_value = (result, 0.001)
    row = score_posting(fake_client, SAMPLE_POSTING, SAMPLE_BULLETS)
    assert row["reports_to"] is None


def test_score_posting_unwraps_list_wrapped_suggested_variant():
    # Found live: Haiku returned suggested_variant as ["leadership"] instead
    # of "leadership" despite the schema declaring a plain string enum.
    # Stored verbatim, this 400s assembly with "unknown variant" the moment
    # the user clicks Approve — 30 of 105 real scores had this exact defect.
    result = {**FAKE_MODEL_RESULT, "suggested_variant": ["leadership"]}
    fake_client = MagicMock()
    fake_client.structured_call.return_value = (result, 0.001)
    row = score_posting(fake_client, SAMPLE_POSTING, SAMPLE_BULLETS)
    assert row["suggested_variant"] == "leadership"


def test_score_posting_discards_invalid_suggested_variant():
    for junk in ("", "<UNKNOWN>", "null", "not-a-real-variant", [], None):
        result = {**FAKE_MODEL_RESULT, "suggested_variant": junk}
        fake_client = MagicMock()
        fake_client.structured_call.return_value = (result, 0.001)
        row = score_posting(fake_client, SAMPLE_POSTING, SAMPLE_BULLETS)
        assert row["suggested_variant"] is None, f"junk value {junk!r} should normalize to None"


def test_score_posting_survives_missing_extraction_field():
    # The schema marks coding_interview_signals (and friends) required, but
    # tool-use generation completeness isn't guaranteed — seen live: Haiku
    # dropped it on a real JD and score_posting KeyError'd, losing the whole
    # score. Missing extraction fields should default, not blow up scoring.
    result = {k: v for k, v in FAKE_MODEL_RESULT.items() if k != "coding_interview_signals"}
    fake_client = MagicMock()
    fake_client.structured_call.return_value = (result, 0.001)
    row = score_posting(fake_client, SAMPLE_POSTING, SAMPLE_BULLETS)
    assert row["coding_interview_signals"] == []
    assert row["total"] == 78  # the core score itself is untouched
