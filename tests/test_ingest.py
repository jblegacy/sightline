from typing import Any

from sightline.ingest import handle_webhook_event, job_to_posting

WEBHOOK_URL = "https://example.com/webhooks/theirstack"


class FakeDB:
    def __init__(self, red_flag_phrases: list[str] | None = None, queue_min_score: int = 55) -> None:
        self.companies: dict[str, int] = {}
        self.postings: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.scores: list[dict[str, Any]] = []
        self._settings = {
            "red_flag_phrases": red_flag_phrases or [],
            "queue_min_score": queue_min_score,
            "monthly_credits": 200,
            "score_threshold": 70,
            "title_include": [],
            "title_exclude": [],
            "queue_salary_min": 0,
            "queue_salary_max": 500000,
            "queue_max_age_days": 21,
            "queue_ko_tolerance": 9,
            "queue_include_no_salary": True,
        }
        self._next_id = 1

    def upsert_company(self, name: str, domain: str | None) -> int:
        key = domain or name
        if key not in self.companies:
            self.companies[key] = self._next_id
            self._next_id += 1
        return self.companies[key]

    def upsert_posting(self, posting: dict[str, Any]) -> dict[str, Any]:
        existing = self.postings.get(posting["external_id"])
        row = {**(existing or {}), **posting}
        row.setdefault("id", self._next_id)
        row.setdefault("first_seen_at", "2026-08-03T00:00:00+00:00")  # mimics the real column's default now()
        if not existing:
            self._next_id += 1
        self.postings[posting["external_id"]] = row
        return row

    def update_posting(self, posting_id: int, fields: dict[str, Any]) -> None:
        for row in self.postings.values():
            if row["id"] == posting_id:
                row.update(fields)

    def mark_posting_closed(self, external_id: str, closed_at: str) -> None:
        if external_id in self.postings:
            self.postings[external_id]["status"] = "expired"
            self.postings[external_id]["closed_at"] = closed_at

    def log_event(self, entity_type, event, entity_id=None, payload=None) -> None:
        self.events.append(
            {"entity_type": entity_type, "event": event, "entity_id": entity_id, "payload": payload}
        )

    def get_settings(self) -> dict[str, Any]:
        return self._settings

    def get_bullets(self) -> list[dict[str, Any]]:
        return [{"ref": "BL-001", "text": "Sample bullet.", "tags": ["automation"], "variants": ["engineer"]}]

    def list_scored_postings(self) -> list[dict[str, Any]]:
        result = []
        for row in self.postings.values():
            if row.get("status") != "scored":
                continue
            matching_scores = [s for s in self.scores if s.get("posting_id") == row["id"]]
            result.append({**row, "companies": {"name": "Fake Co"}, "scores": matching_scores})
        return result

    def insert_score(self, score: dict[str, Any]) -> dict[str, Any]:
        row = {**score, "id": len(self.scores) + 1}
        self.scores.append(row)
        return row


class FakeAnthropic:
    """Stand-in for AnthropicClient — score_posting only calls structured_call.
    Only `dimensions` matters: score_posting recomputes total from it rather
    than trusting the model's own stated total."""

    def __init__(self, dimensions: dict[str, int] | None = None) -> None:
        self.dimensions = dimensions or {
            "role_fit": 20, "evidence_overlap": 15, "seniority_scope": 12,
            "remote_authenticity": 15, "comp_signal": 10, "company_stage_fit": 8, "red_flags": 0,
        }

    def structured_call(self, **kwargs):
        result = {
            "dimensions": self.dimensions,
            "total": sum(self.dimensions.values()),
            "rationale": "test rationale",
            "keywords": ["automation"],
            "matched_bullet_refs": ["BL-001"],
            "unmet_requirements": [],
            "knockouts": [],
            "suggested_variant": "engineer",
            "reports_to": "",
            "named_contacts": [],
            "target_titles": [],
            "company_signals": [],
        }
        return result, 0.002


class FakeTheirStack:
    """Stand-in for TheirStackClient — only what check_and_enforce_budget calls.
    Defaults to well under budget so the circuit breaker never trips in tests
    that aren't specifically exercising it."""

    def __init__(self, used_api_credits: int = 10) -> None:
        self.used_api_credits = used_api_credits
        self.disabled_search = False
        self.disabled_webhook = False

    def credit_balance(self):
        return {"api_credits": 200, "used_api_credits": self.used_api_credits}

    def find_saved_search(self, name):
        return {"id": 1, "is_alert_active": True}

    def find_webhook(self, url):
        return {"id": 1, "is_active": True}

    def set_saved_search_active(self, search_id, is_active):
        self.disabled_search = not is_active

    def set_webhook_active(self, webhook_id, is_active):
        self.disabled_webhook = not is_active


def dispatch(db, event, anthropic=None, theirstack=None):
    return handle_webhook_event(
        db, anthropic or FakeAnthropic(), theirstack or FakeTheirStack(), WEBHOOK_URL, event
    )


SAMPLE_JOB = {
    "id": 999001,
    "job_title": "AI Automation Engineer",
    "url": "https://example.com/jobs/999001",
    "final_url": "https://acme.com/careers/999001",
    "description": "Build automation systems.",
    "date_posted": "2026-08-01",
    "remote": True,
    "location": "US Remote",
    "min_annual_salary_usd": 140000,
    "max_annual_salary_usd": 170000,
    "company": "Acme Inc",
    "company_domain": "acme.com",
}


def test_job_to_posting_prefers_final_url():
    posting = job_to_posting(SAMPLE_JOB, company_id=1)
    assert posting["url"] == "https://acme.com/careers/999001"


def test_job_to_posting_comp_source_posted_when_salary_present():
    posting = job_to_posting(SAMPLE_JOB, company_id=1)
    assert posting["comp_source"] == "posted"
    assert posting["comp_min"] == 140000


def test_job_to_posting_comp_source_absent_when_no_salary():
    job = {**SAMPLE_JOB, "min_annual_salary_usd": None, "max_annual_salary_usd": None}
    posting = job_to_posting(job, company_id=1)
    assert posting["comp_source"] == "absent"


def test_job_to_posting_remote_flag_unclear_when_null():
    job = {**SAMPLE_JOB, "remote": None}
    posting = job_to_posting(job, company_id=1)
    assert posting["remote_flag"] == "unclear"


def test_handle_job_new_upserts_company_and_posting_and_logs_event():
    db = FakeDB()
    event = {"id": 1, "type": "job.new", "payload": SAMPLE_JOB}
    result = dispatch(db, event)
    assert result["ok"] is True
    assert "999001" in db.postings
    assert db.events[0]["event"] == "ingested"
    assert db.events[0]["payload"]["credits_consumed"] == 1


def test_handle_job_new_is_idempotent_on_duplicate_delivery():
    db = FakeDB()
    event = {"id": 1, "type": "job.new", "payload": SAMPLE_JOB}
    dispatch(db, event)
    dispatch(db, event)  # TheirStack docs: duplicates possible in edge cases
    assert len(db.postings) == 1  # one row, not two


def test_handle_job_closed_marks_posting_expired():
    db = FakeDB()
    dispatch(db, {"id": 1, "type": "job.new", "payload": SAMPLE_JOB})
    dispatch(db, {"id": 2, "type": "job.closed", "payload": {"id": 999001, "closed_at": "2026-08-10T00:00:00Z"}})
    assert db.postings["999001"]["status"] == "expired"


def test_handle_job_closed_for_unknown_posting_does_not_raise():
    db = FakeDB()
    result = dispatch(db, {"id": 1, "type": "job.closed", "payload": {"id": 424242, "closed_at": "2026-08-10T00:00:00Z"}})
    assert result["ok"] is True


def test_handle_webhook_event_ignores_unhandled_type():
    db = FakeDB()
    result = dispatch(db, {"id": 1, "type": "company.new", "payload": {}})
    assert result["ignored"] is True
    assert db.events[-1]["event"] == "unhandled_event_type"


# ---- real-time filter + score wiring ----


def test_not_remote_archives_without_scoring():
    db = FakeDB()
    job = {**SAMPLE_JOB, "remote": False}
    dispatch(db, {"id": 1, "type": "job.new", "payload": job})
    posting = db.postings["999001"]
    assert posting["status"] == "archived"
    assert posting["filter_reason"] == "not remote"
    assert db.scores == []  # never reached scoring — no credits spent on an already-archived posting


def test_red_flag_phrase_archives_without_scoring():
    db = FakeDB(red_flag_phrases=["must have active real estate license"])
    job = {**SAMPLE_JOB, "description": "Must have active real estate license."}
    dispatch(db, {"id": 1, "type": "job.new", "payload": job})
    posting = db.postings["999001"]
    assert posting["status"] == "archived"
    assert db.scores == []


def test_high_score_survives_as_scored():
    db = FakeDB(queue_min_score=55)
    dispatch(db, {"id": 1, "type": "job.new", "payload": SAMPLE_JOB})
    posting = db.postings["999001"]
    assert posting["status"] == "scored"
    assert len(db.scores) == 1
    assert db.scores[0]["total"] == 80


def test_low_score_archives_with_reason():
    # score_posting recomputes total from dimensions (not the model's stated
    # total), so the fixture needs dimensions that actually sum to 30.
    db = FakeDB(queue_min_score=55)
    low_dimensions = {
        "role_fit": 8, "evidence_overlap": 5, "seniority_scope": 4,
        "remote_authenticity": 5, "comp_signal": 4, "company_stage_fit": 4, "red_flags": 0,
    }
    assert sum(low_dimensions.values()) == 30
    dispatch(db, {"id": 1, "type": "job.new", "payload": SAMPLE_JOB}, anthropic=FakeAnthropic(dimensions=low_dimensions))
    posting = db.postings["999001"]
    assert posting["status"] == "archived"
    assert "30" in posting["filter_reason"]
    assert len(db.scores) == 1  # still scored and kept, per spec: archive with rationale retained


def test_score_logs_cost_event():
    db = FakeDB()
    dispatch(db, {"id": 1, "type": "job.new", "payload": SAMPLE_JOB})
    scored_events = [e for e in db.events if e["event"] == "scored"]
    assert len(scored_events) == 1
    assert scored_events[0]["payload"]["cost_usd"] == 0.002


# ---- credit circuit breaker wiring ----


def test_over_budget_trips_circuit_breaker_after_processing():
    db = FakeDB()
    ts = FakeTheirStack(used_api_credits=190)  # >90% of 200
    dispatch(db, {"id": 1, "type": "job.new", "payload": SAMPLE_JOB}, theirstack=ts)
    assert ts.disabled_search is True
    assert ts.disabled_webhook is True
    # the posting itself still processed normally — the breaker can't undo an
    # already-spent credit, it only stops future ones
    assert db.postings["999001"]["status"] in ("scored", "archived")


def test_under_budget_does_not_trip_circuit_breaker():
    db = FakeDB()
    ts = FakeTheirStack(used_api_credits=10)
    dispatch(db, {"id": 1, "type": "job.new", "payload": SAMPLE_JOB}, theirstack=ts)
    assert ts.disabled_search is False
    assert ts.disabled_webhook is False


def test_job_closed_also_checked_against_budget():
    db = FakeDB()
    ts = FakeTheirStack(used_api_credits=10)
    dispatch(db, {"id": 1, "type": "job.new", "payload": SAMPLE_JOB}, theirstack=ts)
    ts.used_api_credits = 190
    dispatch(
        db, {"id": 2, "type": "job.closed", "payload": {"id": 999001, "closed_at": "2026-08-10T00:00:00Z"}},
        theirstack=ts,
    )
    assert ts.disabled_search is True
