from unittest.mock import MagicMock

from sightline.settings_service import preview_query, update_settings


def make_db(updated_row):
    db = MagicMock()
    db.update_settings.return_value = updated_row
    return db


def test_update_settings_syncs_theirstack_when_fetch_field_changes():
    updated = {"title_include": ["ai engineer"], "title_exclude": [], "remote_only": True,
               "open_only": True, "direct_employer": True, "countries": ["US"],
               "min_employee_count": 50, "employment_types": ["full_time"]}
    db = make_db(updated)
    ts = MagicMock()
    result = update_settings(db, ts, {"title_include": ["ai engineer"]})
    assert result == updated
    ts.upsert_saved_search.assert_called_once()


def test_update_settings_does_not_sync_for_non_fetch_fields():
    updated = {"queue_min_score": 60}
    db = make_db(updated)
    ts = MagicMock()
    update_settings(db, ts, {"queue_min_score": 60})
    ts.upsert_saved_search.assert_not_called()


def test_update_settings_syncs_on_partial_fetch_field_overlap():
    updated = {"title_include": [], "title_exclude": [], "remote_only": True}
    db = make_db(updated)
    ts = MagicMock()
    # request touches both a fetch field and a non-fetch field
    update_settings(db, ts, {"remote_only": True, "queue_min_score": 60})
    ts.upsert_saved_search.assert_called_once()


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
