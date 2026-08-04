from unittest.mock import MagicMock

from sightline.settings_service import preview_query, update_search_profile, update_settings

PROFILES = [
    {"id": "automation", "title_include": ["workflow automation"], "title_exclude": ["ai engineer"]},
    {"id": "cpg", "title_include": ["director of operations"], "title_exclude": ["forklift"]},
]


def make_db(updated_settings_row, profiles=None):
    db = MagicMock()
    db.update_settings.return_value = updated_settings_row
    db.get_settings.return_value = updated_settings_row
    db.get_search_profiles.return_value = profiles if profiles is not None else PROFILES
    return db


def test_update_settings_syncs_both_profiles_when_shared_field_changes():
    updated = {"remote_only": True, "open_only": True, "direct_employer": True,
               "countries": ["US"], "min_employee_count": 50, "employment_types": ["full_time"]}
    db = make_db(updated)
    ts = MagicMock()
    result = update_settings(db, ts, {"remote_only": True})
    assert result == updated
    assert ts.upsert_saved_search.call_count == 2  # both profiles re-synced


def test_update_settings_does_not_sync_for_non_fetch_fields():
    updated = {"queue_min_score": 60}
    db = make_db(updated)
    ts = MagicMock()
    update_settings(db, ts, {"queue_min_score": 60})
    ts.upsert_saved_search.assert_not_called()


def test_update_settings_syncs_on_partial_field_overlap():
    updated = {"remote_only": True}
    db = make_db(updated)
    ts = MagicMock()
    # request touches both a shared fetch field and a non-fetch field
    update_settings(db, ts, {"remote_only": True, "queue_min_score": 60})
    assert ts.upsert_saved_search.call_count == 2


def test_update_settings_syncs_when_seniority_or_source_exclude_change():
    # regression: these were previously missing from the sync-trigger set
    db = make_db({"seniority": ["senior"]})
    ts = MagicMock()
    update_settings(db, ts, {"seniority": ["senior"]})
    assert ts.upsert_saved_search.call_count == 2


def test_update_search_profile_syncs_only_that_profile_when_titles_change():
    db = MagicMock()
    db.update_search_profile.return_value = {"id": "automation", "title_include": ["x"], "title_exclude": []}
    db.get_settings.return_value = {"remote_only": True}
    ts = MagicMock()
    result = update_search_profile(db, ts, "automation", {"title_include": ["x"]})
    assert result["id"] == "automation"
    ts.upsert_saved_search.assert_called_once()
    assert ts.upsert_saved_search.call_args.args[0] == "sightline-automation"


def test_update_search_profile_does_not_sync_for_budget_share_alone():
    db = MagicMock()
    db.update_search_profile.return_value = {"id": "cpg", "budget_share": 0.5}
    ts = MagicMock()
    update_search_profile(db, ts, "cpg", {"budget_share": 0.5})
    ts.upsert_saved_search.assert_not_called()


def test_preview_query_uses_7day_count_for_daily_estimate():
    ts = MagicMock()
    ts.free_count.side_effect = [140, 5000]  # week=140, backlog=5000
    ts.preview.return_value = {"data": [{"job_title": "x"}]}
    result = preview_query(ts, {"posted_at_max_age_days": 30})
    assert result["day"] == 20.0  # 140/7, not a separate noisy 1-day call
    assert result["week"] == 140
    assert result["backlog"] == 5000
    assert result["sample"] == [{"job_title": "x"}]


def test_preview_query_calls_free_count_with_correct_windows():
    ts = MagicMock()
    ts.free_count.side_effect = [10, 20]
    ts.preview.return_value = {"data": []}
    preview_query(ts, {"remote": True})
    calls = ts.free_count.call_args_list
    assert calls[0].args[0]["posted_at_max_age_days"] == 7
    assert calls[1].args[0]["posted_at_max_age_days"] == 3650
