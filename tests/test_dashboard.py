from datetime import datetime, timedelta, timezone

from sightline.dashboard import (
    _age_string,
    _dimensions_array,
    posting_row_to_p,
    postings_to_dashboard_p,
    settings_to_cfg_qv,
)

SAMPLE_SCORE = {
    "total": 88,
    "dimensions": {
        "role_fit": 22, "evidence_overlap": 15, "seniority_scope": 13,
        "remote_authenticity": 15, "comp_signal": 14, "company_stage_fit": 9, "red_flags": 0,
    },
    "rationale": "Strong fit.",
    "keywords": ["AI tooling"],
    "matched_bullet_ids": ["BL-002"],
    "unmet_requirements": ["Named enterprise stack"],
    "knockouts": ["5+ years required"],
    "coding_interview_signals": ["system design interview"],
    "suggested_variant": "leadership",
    "reports_to": "VP Engineering",
    "named_contacts": [{"name": "Jane Doe", "title": "CTO", "context": "named in JD"}],
    "target_titles": ["VP Engineering"],
    "company_signals": ["Raised Series C"],
}

SAMPLE_ROW = {
    "id": 10,
    "company_id": 3,
    "title": "AI Enablement Lead",
    "url": "https://boards.greenhouse.io/acme/jobs/12345",
    "location_raw": "US Remote",
    "first_seen_at": datetime.now(timezone.utc).isoformat(),
    "comp_min": 160000,
    "comp_max": 190000,
    "comp_source": "posted",
    "companies": {"id": 3, "name": "Acme Robotics"},
    "scores": [SAMPLE_SCORE],
}


def test_age_string_hours_under_a_day():
    ts = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    assert _age_string(ts) == "5h"


def test_age_string_days_over_a_day():
    ts = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    assert _age_string(ts) == "3d"


def test_age_string_handles_z_suffix():
    ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    assert _age_string(ts) == "2h"


def test_dimensions_array_matches_dims_order():
    arr = _dimensions_array(SAMPLE_SCORE["dimensions"])
    assert arr == [22, 15, 13, 15, 14, 9, 0]


def test_posting_row_to_p_maps_core_fields():
    p = posting_row_to_p(SAMPLE_ROW, co_count=1)
    assert p["id"] == 10
    assert p["co"] == "Acme Robotics"
    assert p["ti"] == "AI Enablement Lead"
    assert p["score"] == 88
    assert p["v"] == "lead"
    assert p["ko"] == ["5+ years required"]
    assert p["gaps"] == ["Named enterprise stack"]
    assert p["rt"] == "VP Engineering"
    assert p["nc"] == [{"n": "Jane Doe", "t": "CTO"}]
    assert p["ci"] == ["system design interview"]
    assert p["url"] == "https://boards.greenhouse.io/acme/jobs/12345"


def test_posting_row_to_p_returns_none_without_a_score():
    row = {**SAMPLE_ROW, "scores": []}
    assert posting_row_to_p(row, co_count=1) is None


def test_posting_row_to_p_no_override_uses_ai_total():
    p = posting_row_to_p(SAMPLE_ROW, co_count=1)
    assert p["score"] == 88
    assert p["aiScore"] == 88
    assert p["scoreOverride"] is None


def test_posting_row_to_p_override_becomes_effective_score():
    score = {
        **SAMPLE_SCORE, "total": 51,
        "human_override_total": 78, "human_override_reason": "JD reads as a real fit, not noise",
    }
    row = {**SAMPLE_ROW, "scores": [score]}
    p = posting_row_to_p(row, co_count=1)
    assert p["score"] == 78  # effective score used for queue/watch bucketing
    assert p["aiScore"] == 51  # original AI total preserved, not overwritten
    assert p["scoreOverride"] == {"total": 78, "reason": "JD reads as a real fit, not noise"}


def test_postings_to_dashboard_p_override_moves_posting_into_queue():
    score = {**SAMPLE_SCORE, "total": 51, "human_override_total": 78, "human_override_reason": "underscored"}
    row = {**SAMPLE_ROW, "scores": [score]}
    result = postings_to_dashboard_p([row], score_threshold=70)
    assert result[0]["stage"] == "queue"


def test_postings_to_dashboard_p_queued_status_reverses_approve_back_to_queue():
    # "Back to queue" on an approved posting — reverses Approve without
    # discarding an already-built variant.
    variant = {"posting_id": 10, "kind": "leadership", "storage_path": "10/leadership-x.docx", "brief": ""}
    application = {"posting_id": 10, "status": "queued", "submitted_at": None}
    row = {**SAMPLE_ROW, "variants": [variant], "applications": application}
    result = postings_to_dashboard_p([row], score_threshold=70)
    assert result[0]["stage"] == "queue"


def test_postings_to_dashboard_p_buckets_queue_vs_watch():
    high = SAMPLE_ROW
    low_score = {**SAMPLE_SCORE, "total": 60, "dimensions": {
        "role_fit": 15, "evidence_overlap": 10, "seniority_scope": 10,
        "remote_authenticity": 10, "comp_signal": 10, "company_stage_fit": 5, "red_flags": 0,
    }}
    low = {**SAMPLE_ROW, "id": 11, "scores": [low_score]}
    result = postings_to_dashboard_p([high, low], score_threshold=70)
    stages = {p["id"]: p["stage"] for p in result}
    assert stages[10] == "queue"
    assert stages[11] == "watch"


def test_postings_to_dashboard_p_counts_roles_per_company():
    a = {**SAMPLE_ROW, "id": 1, "company_id": 3}
    b = {**SAMPLE_ROW, "id": 2, "company_id": 3}
    c = {**SAMPLE_ROW, "id": 3, "company_id": 4}
    result = postings_to_dashboard_p([a, b, c], score_threshold=70)
    co_counts = {p["id"]: p["coCount"] for p in result}
    assert co_counts[1] == 2
    assert co_counts[2] == 2
    assert co_counts[3] == 1


def test_settings_to_cfg_qv_shape():
    settings = {"queue_min_score": 55, "score_threshold": 70}
    out = settings_to_cfg_qv(settings)
    assert out["qv"]["score"] == 55
    assert out["scoreThreshold"] == 70
    assert "cfg" not in out  # title lists moved to search_profiles


def test_search_profiles_to_dashboard_shape():
    from sightline.dashboard import search_profiles_to_dashboard

    profiles = [
        {"id": "automation", "label": "AI / Workflow Automation",
         "title_include": ["workflow automation"], "title_exclude": ["ai engineer"],
         "resume_variant": "engineer", "budget_share": 0.6},
        {"id": "cpg", "label": "CPG Operations",
         "title_include": ["director of operations"], "title_exclude": ["forklift"],
         "resume_variant": "leadership", "budget_share": 0.4},
    ]
    out = search_profiles_to_dashboard(profiles)
    assert out[0] == {
        "id": "automation", "label": "AI / Workflow Automation",
        "inc": ["workflow automation"], "exc": ["ai engineer"],
        "variant": "engineer", "budgetShare": 0.6,
    }
    assert out[1]["id"] == "cpg"
