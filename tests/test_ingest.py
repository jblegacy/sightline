from typing import Any

from sightline.ingest import handle_webhook_event, job_to_posting


class FakeDB:
    def __init__(self) -> None:
        self.companies: dict[str, int] = {}
        self.postings: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
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
        if not existing:
            self._next_id += 1
        self.postings[posting["external_id"]] = row
        return row

    def mark_posting_closed(self, external_id: str, closed_at: str) -> None:
        if external_id in self.postings:
            self.postings[external_id]["status"] = "expired"
            self.postings[external_id]["closed_at"] = closed_at

    def log_event(self, entity_type, event, entity_id=None, payload=None) -> None:
        self.events.append(
            {"entity_type": entity_type, "event": event, "entity_id": entity_id, "payload": payload}
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
    result = handle_webhook_event(db, event)
    assert result["ok"] is True
    assert "999001" in db.postings
    assert db.events[-1]["event"] == "ingested"
    assert db.events[-1]["payload"]["credits_consumed"] == 1


def test_handle_job_new_is_idempotent_on_duplicate_delivery():
    db = FakeDB()
    event = {"id": 1, "type": "job.new", "payload": SAMPLE_JOB}
    handle_webhook_event(db, event)
    handle_webhook_event(db, event)  # TheirStack docs: duplicates are possible in edge cases
    assert len(db.postings) == 1  # one row, not two
    assert len(db.events) == 2  # but each delivery is still logged — it was still billed


def test_handle_job_closed_marks_posting_expired():
    db = FakeDB()
    handle_webhook_event(db, {"id": 1, "type": "job.new", "payload": SAMPLE_JOB})
    handle_webhook_event(
        db, {"id": 2, "type": "job.closed", "payload": {"id": 999001, "closed_at": "2026-08-10T00:00:00Z"}}
    )
    assert db.postings["999001"]["status"] == "expired"


def test_handle_job_closed_for_unknown_posting_does_not_raise():
    db = FakeDB()
    result = handle_webhook_event(
        db, {"id": 1, "type": "job.closed", "payload": {"id": 424242, "closed_at": "2026-08-10T00:00:00Z"}}
    )
    assert result["ok"] is True


def test_handle_webhook_event_ignores_unhandled_type():
    db = FakeDB()
    result = handle_webhook_event(db, {"id": 1, "type": "company.new", "payload": {}})
    assert result["ignored"] is True
    assert db.events[-1]["event"] == "unhandled_event_type"
