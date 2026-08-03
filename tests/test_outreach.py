from typing import Any

import pytest

from sightline.outreach import (
    assemble_outreach,
    generate_drafts,
    linkedin_search_url,
    select_metric_bullet,
)
from sightline.provenance import ProvenanceError
from tests.test_ingest import FakeAnthropic, FakeDB


def make_bullet(
    ref: str, variants: list[str], tags: list[str],
    provenance: str = "measured", status: str = "verified",
) -> dict[str, Any]:
    return {
        "id": int(ref.split("-")[1]), "ref": ref, "text": f"Claim for {ref}.",
        "source_org": "BEAM LEGACY GROUP", "source_period": "2025-Present",
        "tags": tags, "variants": variants, "provenance": provenance, "status": status,
    }


BULLETS = [
    make_bullet("BL-001", ["engineer"], ["production"]),
    make_bullet("BL-002", ["engineer"], ["llm", "cost"]),
    make_bullet("BL-003", ["leadership"], ["scale"]),
]


# ---- linkedin_search_url ----


def test_linkedin_search_url_encodes_company_and_first_title():
    url = linkedin_search_url("Acme & Co", ["VP Engineering", "Head of Product"])
    assert url.startswith("https://www.linkedin.com/search/results/people/?keywords=")
    assert "Acme" in url
    assert "VP+Engineering" in url or "VP%20Engineering" in url


def test_linkedin_search_url_falls_back_to_company_only():
    url = linkedin_search_url("Acme", [])
    assert "Acme" in url


# ---- select_metric_bullet ----


def test_select_metric_bullet_filters_by_variant():
    b = select_metric_bullet(BULLETS, "leadership", [])
    assert b["ref"] == "BL-003"


def test_select_metric_bullet_picks_highest_overlap():
    b = select_metric_bullet(BULLETS, "engineer", ["LLM cost reduction"])
    assert b["ref"] == "BL-002"


def test_select_metric_bullet_none_when_no_bullets_for_variant():
    assert select_metric_bullet(BULLETS, "engineer", []) is not None
    only_leadership = [make_bullet("BL-009", ["leadership"], ["scale"])]
    assert select_metric_bullet(only_leadership, "engineer", []) is None


# ---- generate_drafts ----


def test_generate_drafts_sends_signal_and_metric_to_anthropic():
    posting = {"title": "AI Engineer", "companies": {"name": "Acme"}}
    score = {"company_signals": ["Raised $10M"], "reports_to": "CTO"}
    metric = make_bullet("BL-002", ["engineer"], ["llm"])
    anthropic = FakeAnthropic()
    drafts, cost = generate_drafts(anthropic, posting, score, "Jane Doe", "VP Eng", metric, {})
    assert set(drafts.keys()) == {"note", "message", "subject", "email"}
    assert cost == 0.006


# ---- assemble_outreach orchestration ----


def make_scored_posting(db: FakeDB, variant: str = "engineer") -> int:
    posting = db.upsert_posting({
        "external_id": "job-1", "title": "AI Engineer", "url": "https://x.com/1",
        "status": "scored",
    })
    db.insert_score({
        "posting_id": posting["id"],
        "rationale": "Strong fit.",
        "keywords": ["llm"],
        "company_signals": ["Raised $10M"],
        "reports_to": "CTO",
        "suggested_variant": variant,
    })
    return posting["id"]


def test_assemble_outreach_requires_target_name():
    db = FakeDB()
    posting_id = make_scored_posting(db)
    with pytest.raises(ValueError, match="target_name"):
        assemble_outreach(db, FakeAnthropic(), posting_id, target_name="")


def test_assemble_outreach_raises_when_unscored():
    db = FakeDB()
    posting = db.upsert_posting({"external_id": "job-2", "title": "X", "url": "y", "status": "scored"})
    with pytest.raises(ValueError, match="not been scored"):
        assemble_outreach(db, FakeAnthropic(), posting["id"], target_name="Jane")


def test_assemble_outreach_blocks_on_unverified_metric_bullet():
    db = FakeDB()
    posting_id = make_scored_posting(db)
    db.get_bullets_full = lambda: [
        {"id": 1, "ref": "BL-050", "text": "Draft claim.", "source_org": "BEAM LEGACY GROUP",
         "source_period": "2025-Present", "tags": ["llm"], "variants": ["engineer"],
         "provenance": "measured", "status": "draft"}
    ]
    with pytest.raises(ProvenanceError):
        assemble_outreach(db, FakeAnthropic(), posting_id, target_name="Jane Doe")
    assert any(e["event"] == "outreach_blocked" for e in db.events)
    assert db.outreach == []


def test_assemble_outreach_happy_path_persists_drafts():
    db = FakeDB()
    posting_id = make_scored_posting(db)
    db.get_bullets_full = lambda: [
        {"id": 1, "ref": "BL-002", "text": "Reduced inference cost via prompt caching.",
         "source_org": "BEAM LEGACY GROUP", "source_period": "2025-Present", "tags": ["llm"],
         "variants": ["engineer"], "provenance": "measured", "status": "verified"}
    ]
    row = assemble_outreach(
        db, FakeAnthropic(), posting_id,
        target_name="Jane Doe", target_title="VP Eng",
        target_linkedin_url="https://linkedin.com/in/janedoe",
    )
    assert row["target_name"] == "Jane Doe"
    assert row["draft_linkedin_note"]
    assert row["draft_email_subject"] == "Question about your automation roadmap"
    assert len(db.outreach) == 1
    assert any(e["event"] == "drafts_generated" for e in db.events)


def test_assemble_outreach_upserts_on_repeat_call():
    db = FakeDB()
    posting_id = make_scored_posting(db)
    db.get_bullets_full = lambda: [
        {"id": 1, "ref": "BL-002", "text": "Reduced inference cost via prompt caching.",
         "source_org": "BEAM LEGACY GROUP", "source_period": "2025-Present", "tags": ["llm"],
         "variants": ["engineer"], "provenance": "measured", "status": "verified"}
    ]
    assemble_outreach(db, FakeAnthropic(), posting_id, target_name="Jane Doe")
    assemble_outreach(db, FakeAnthropic(), posting_id, target_name="Jane Updated")
    assert len(db.outreach) == 1
    assert db.outreach[0]["target_name"] == "Jane Updated"
