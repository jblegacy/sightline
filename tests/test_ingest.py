from typing import Any

from sightline.ingest import classify_search_profile, handle_webhook_event, job_to_posting


class FakeDB:
    def __init__(self, red_flag_phrases: list[str] | None = None, queue_min_score: int = 55) -> None:
        self.companies: dict[str, int] = {}
        self.postings: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.scores: list[dict[str, Any]] = []
        self.variants: list[dict[str, Any]] = []
        self.outreach: list[dict[str, Any]] = []
        self.applications: list[dict[str, Any]] = []
        self.answers: list[dict[str, Any]] = []
        self.uploaded_documents: list[tuple[str, str, bytes]] = []
        self._settings = {
            "red_flag_phrases": red_flag_phrases or [],
            "queue_min_score": queue_min_score,
            "monthly_credits": 200,
            "score_threshold": 70,
            "queue_salary_min": 0,
            "queue_salary_max": 500000,
            "queue_max_age_days": 21,
            "queue_ko_tolerance": 9,
            "queue_include_no_salary": True,
        }
        self._search_profiles = [
            {
                "id": "automation", "label": "AI / Workflow Automation",
                "title_include": ["workflow automation", "automation specialist"],
                "title_exclude": ["ai engineer", "software engineer"],
                "resume_variant": "engineer", "budget_share": 0.6, "active": True,
            },
            {
                "id": "cpg", "label": "CPG Operations",
                "title_include": ["director of operations", "supply chain manager"],
                "title_exclude": ["warehouse associate", "forklift"],
                "resume_variant": "leadership", "budget_share": 0.4, "active": True,
            },
        ]
        self._next_id = 1

    def upsert_company(self, name: str, domain: str | None) -> int:
        key = domain or name
        if key not in self.companies:
            self.companies[key] = self._next_id
            self._next_id += 1
        return self.companies[key]

    def find_posting_by_external_id(self, external_id: str) -> dict[str, Any] | None:
        row = self.postings.get(external_id)
        return {"id": row["id"], "status": row["status"]} if row else None

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
            matching_variants = [v for v in self.variants if v.get("posting_id") == row["id"]]
            # applications.posting_id and outreach.posting_id are both
            # unique — PostgREST embeds them as to-one objects, not arrays
            # (see sightline/dashboard.py).
            application = next(
                (a for a in self.applications if a.get("posting_id") == row["id"]), None
            )
            outreach = next((o for o in self.outreach if o.get("posting_id") == row["id"]), None)
            result.append({
                **row, "companies": {"name": "Fake Co"}, "scores": matching_scores,
                "variants": matching_variants, "outreach": outreach,
                "applications": application,
            })
        return result

    def insert_score(self, score: dict[str, Any]) -> dict[str, Any]:
        row = {**score, "id": len(self.scores) + 1}
        self.scores.append(row)
        return row

    def update_settings(self, fields: dict[str, Any]) -> dict[str, Any]:
        self._settings.update(fields)
        return self._settings

    def get_search_profiles(self) -> list[dict[str, Any]]:
        return self._search_profiles

    def update_search_profile(self, profile_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        for p in self._search_profiles:
            if p["id"] == profile_id:
                p.update(fields)
                return p
        raise LookupError(f"search profile {profile_id!r} not found")

    def get_posting(self, posting_id: int) -> dict[str, Any]:
        for row in self.postings.values():
            if row["id"] == posting_id:
                matching_scores = [s for s in self.scores if s.get("posting_id") == posting_id]
                matching_variants = [v for v in self.variants if v.get("posting_id") == posting_id]
                application = next(
                    (a for a in self.applications if a.get("posting_id") == posting_id), None
                )
                outreach = next(
                    (o for o in self.outreach if o.get("posting_id") == posting_id), None
                )
                return {
                    **row, "companies": {"name": "Fake Co"}, "scores": matching_scores,
                    "variants": matching_variants, "outreach": outreach,
                    "applications": application,
                }
        raise LookupError(f"posting {posting_id} not found")

    def get_bullets_full(self) -> list[dict[str, Any]]:
        return [{
            "id": 1, "ref": "BL-001", "text": "Sample bullet.", "source_org": "BEAM LEGACY GROUP",
            "source_period": "2025-Present", "tags": ["automation"], "variants": ["engineer"],
            "provenance": "measured", "status": "verified",
        }]

    def upload_document(self, bucket: str, path: str, content: bytes) -> None:
        self.uploaded_documents.append((bucket, path, content))

    def create_signed_url(
        self, bucket: str, path: str, expires_in: int = 3600, download_filename: str | None = None
    ) -> str:
        url = f"https://example.supabase.co/storage/v1/object/sign/{bucket}/{path}?token=fake"
        if download_filename:
            url += f"&download={download_filename}"
        return url

    def insert_variant(self, fields: dict[str, Any]) -> dict[str, Any]:
        row = {**fields, "id": len(self.variants) + 1}
        self.variants.append(row)
        return row

    def update_variant(self, variant_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        for v in self.variants:
            if v["id"] == variant_id:
                v.update(fields)
                return v
        raise LookupError(f"variant {variant_id} not found")

    def upsert_outreach(self, fields: dict[str, Any]) -> dict[str, Any]:
        existing = next(
            (o for o in self.outreach if o.get("posting_id") == fields.get("posting_id")), None
        )
        if existing:
            existing.update(fields)
            return existing
        row = {**fields, "id": len(self.outreach) + 1}
        self.outreach.append(row)
        return row

    def mark_outreach_sent(self, posting_id: int, channel: str) -> dict[str, Any]:
        row = next((o for o in self.outreach if o.get("posting_id") == posting_id), None)
        if row is None:
            raise LookupError(f"no outreach row for posting {posting_id}")
        row["sent_at"] = "2026-08-03T12:00:00+00:00"
        row["sent_channel"] = channel
        row["follow_up_due"] = "2026-08-10"
        return row

    def override_score(self, score_id: int, total: int | None, reason: str | None) -> dict[str, Any]:
        for s in self.scores:
            if s["id"] == score_id:
                s["human_override_total"] = total
                s["human_override_reason"] = reason
                s["human_override_at"] = "2026-08-04T12:00:00+00:00" if total is not None else None
                return s
        raise LookupError(f"score {score_id} not found")

    def get_answers(self) -> list[dict[str, Any]]:
        return self.answers

    def upsert_answer(self, fields: dict[str, Any]) -> dict[str, Any]:
        existing = next((a for a in self.answers if a.get("ref") == fields.get("ref")), None)
        if existing:
            existing.update(fields)
            return existing
        row = {**fields, "id": len(self.answers) + 1}
        self.answers.append(row)
        return row

    def upsert_application(self, fields: dict[str, Any]) -> dict[str, Any]:
        existing = next(
            (a for a in self.applications if a.get("posting_id") == fields.get("posting_id")), None
        )
        if existing:
            existing.update(fields)
            return existing
        row = {**fields, "id": len(self.applications) + 1}
        self.applications.append(row)
        return row

    def count_postings_since(self, since_iso: str) -> int:
        return sum(1 for r in self.postings.values() if r.get("first_seen_at", "") >= since_iso)

    def count_scored_above_since(self, since_iso: str, score_threshold: int) -> int:
        count = 0
        for row in self.postings.values():
            if row.get("status") != "scored" or row.get("first_seen_at", "") < since_iso:
                continue
            matching_scores = [s for s in self.scores if s.get("posting_id") == row["id"]]
            if matching_scores and matching_scores[-1].get("total", 0) >= score_threshold:
                count += 1
        return count

    def submitted_applications_since(self, since_iso: str) -> list[dict[str, Any]]:
        result = []
        for a in self.applications:
            submitted_at = a.get("submitted_at")
            if not submitted_at or submitted_at < since_iso:
                continue
            posting = next(
                (r for r in self.postings.values() if r["id"] == a.get("posting_id")), None
            )
            result.append({
                "submitted_at": submitted_at,
                "postings": {"first_seen_at": posting["first_seen_at"]} if posting else None,
            })
        return result

    def replied_outreach_since(self, since_iso: str) -> int:
        return sum(
            1 for o in self.outreach if o.get("replied_at") and o["replied_at"] >= since_iso
        )

    def scoring_cost_since(self, since_iso: str) -> float:
        return sum(
            s.get("cost_usd") or 0
            for s in self.scores
            if s.get("created_at", "") >= since_iso
        )


class FakeAnthropic:
    """Stand-in for AnthropicClient — score_posting only calls structured_call.
    Only `dimensions` matters: score_posting recomputes total from it rather
    than trusting the model's own stated total."""

    def __init__(self, dimensions: dict[str, int] | None = None) -> None:
        self.dimensions = dimensions or {
            "role_fit": 20, "evidence_overlap": 15, "seniority_scope": 12,
            "remote_authenticity": 15, "comp_signal": 10, "company_stage_fit": 8, "red_flags": 0,
        }

    def chat_call(self, **kwargs):
        return "Here's a draft answer grounded in your verified bullets.", 0.008

    def structured_call(self, **kwargs):
        if kwargs.get("tool_name") == "submit_brief":
            return {"brief": "Lead with the production system."}, 0.004
        if kwargs.get("tool_name") == "submit_drafts":
            return {
                "note": "Saw your team is scaling fast. I build production AI automation. Worth a chat?",
                "message": "Hi there,\n\nSaw the posting — impressive growth. I build automation "
                            "systems end to end.\n\nWorth 15 minutes?\n\nJames",
                "subject": "Question about your automation roadmap",
                "email": "Hi there,\n\nSaw the posting mentions scaling fast. I build this kind of "
                         "system.\n\nWorth a 15-minute conversation?\n\nJames",
            }, 0.006
        result = {
            "dimensions": self.dimensions,
            "total": sum(self.dimensions.values()),
            "rationale": "test rationale",
            "keywords": ["automation"],
            "matched_bullet_refs": ["BL-001"],
            "unmet_requirements": [],
            "knockouts": [],
            "coding_interview_signals": [],
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

    def find_webhook_for_search(self, search_id):
        return {"id": 1, "is_active": True}

    def set_saved_search_active(self, search_id, is_active):
        self.disabled_search = not is_active

    def set_webhook_active(self, webhook_id, is_active):
        self.disabled_webhook = not is_active

    def upsert_saved_search(self, name, filters):
        self.last_synced_filters = filters
        return {"id": 1, "name": name}

    def free_count(self, filters):
        return 42

    def preview(self, filters, limit=25):
        return {"data": [{"job_title": "Sample Job", "has_blurred_data": True}]}


def dispatch(db, event, anthropic=None, theirstack=None):
    return handle_webhook_event(db, anthropic or FakeAnthropic(), theirstack or FakeTheirStack(), event)


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


def test_job_to_posting_rounds_float_salary_to_int():
    # TheirStack sends salary as a float sometimes (e.g. 208705.0) — postings.
    # comp_min/comp_max are int columns; passing the float through verbatim
    # 400s the insert (Postgres rejects "208705.0" as invalid integer input).
    job = {**SAMPLE_JOB, "min_annual_salary_usd": 208705.0, "max_annual_salary_usd": 230000.6}
    posting = job_to_posting(job, company_id=1)
    assert posting["comp_min"] == 208705
    assert posting["comp_max"] == 230001
    assert isinstance(posting["comp_min"], int)
    assert isinstance(posting["comp_max"], int)


# ---- classify_search_profile ----

_PROFILES = [
    {"id": "automation", "title_include": ["workflow automation", "automation specialist"],
     "title_exclude": ["ai engineer"]},
    {"id": "cpg", "title_include": ["director of operations"], "title_exclude": ["forklift"]},
]


def test_classify_search_profile_matches_automation():
    assert classify_search_profile("Automation Specialist", _PROFILES) == "automation"


def test_classify_search_profile_matches_cpg():
    assert classify_search_profile("Director of Operations", _PROFILES) == "cpg"


def test_classify_search_profile_excluded_term_skips_that_profile():
    # "AI Automation Engineer" hits both automation's include and exclude terms
    assert classify_search_profile("AI Automation Engineer", _PROFILES) is None


def test_classify_search_profile_no_match_returns_none():
    assert classify_search_profile("Forklift Operator", _PROFILES) is None


def test_handle_job_new_upserts_company_and_posting_and_logs_event():
    db = FakeDB()
    event = {"id": 1, "type": "job.new", "payload": SAMPLE_JOB}
    result = dispatch(db, event)
    assert result["ok"] is True
    assert "999001" in db.postings
    assert db.events[0]["event"] == "ingested"
    assert db.events[0]["payload"]["credits_consumed"] == 1


def test_handle_job_new_stores_classified_search_profile_id():
    db = FakeDB()
    job = {**SAMPLE_JOB, "job_title": "Automation Specialist"}  # matches FakeDB's automation profile
    dispatch(db, {"id": 1, "type": "job.new", "payload": job})
    assert db.postings["999001"]["search_profile_id"] == "automation"


def test_handle_job_new_is_idempotent_on_duplicate_delivery():
    db = FakeDB()
    event = {"id": 1, "type": "job.new", "payload": SAMPLE_JOB}
    dispatch(db, event)
    dispatch(db, event)  # TheirStack docs: duplicates possible in edge cases
    assert len(db.postings) == 1  # one row, not two


def test_handle_job_new_does_not_rescore_a_redelivered_match():
    """Regression: TheirStack redelivers job.new for a still-open match on
    every scan cycle of an active alert, not just once — observed in
    production burning a credit and an Anthropic call every ~hour for the
    same 3 jobs. The posting must not be re-filtered/re-scored once it's
    already past ingest."""
    db = FakeDB()
    event = {"id": 1, "type": "job.new", "payload": SAMPLE_JOB}
    dispatch(db, event)
    scores_after_first = len(db.scores)
    assert scores_after_first == 1

    dispatch(db, event)
    dispatch(db, event)
    assert len(db.scores) == scores_after_first  # no new scoring calls
    assert db.postings["999001"]["status"] == "scored"  # not reset to 'new'
    assert db.events[-1]["event"] == "duplicate_delivery"
    assert db.events[-1]["payload"]["credits_consumed"] == 1


def test_handle_job_new_rescores_when_previous_delivery_was_archived():
    """A redelivery of a posting that was archived (below queue_min_score,
    or filtered out) should also short-circuit — not just 'scored'."""
    db = FakeDB(queue_min_score=999)  # forces archive-on-score for any total
    event = {"id": 1, "type": "job.new", "payload": SAMPLE_JOB}
    dispatch(db, event)
    assert db.postings["999001"]["status"] == "archived"
    scores_after_first = len(db.scores)

    dispatch(db, event)
    assert len(db.scores) == scores_after_first
    assert db.events[-1]["event"] == "duplicate_delivery"


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


def test_daily_cap_trips_end_to_end_even_when_under_monthly_budget():
    db = FakeDB()
    db._settings["daily_credit_cap"] = 1
    ts = FakeTheirStack(used_api_credits=10)  # well under the monthly budget
    # first event of the day just establishes the baseline — can't trip yet
    dispatch(db, {"id": 1, "type": "job.new", "payload": SAMPLE_JOB}, theirstack=ts)
    assert ts.disabled_search is False

    ts.used_api_credits = 11  # a real credit got spent since the baseline
    other_job = {**SAMPLE_JOB, "id": 999002}
    dispatch(db, {"id": 2, "type": "job.new", "payload": other_job}, theirstack=ts)
    assert ts.disabled_search is True
    assert ts.disabled_webhook is True
    assert any(e["event"] == "daily_cap_tripped" for e in db.events)


def test_daily_cap_unset_does_not_trip():
    db = FakeDB()  # daily_credit_cap not in settings -> None -> throttle off
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
