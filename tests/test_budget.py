import datetime as _dt
from unittest.mock import MagicMock

from sightline.budget import (
    check_and_enforce_budget,
    check_and_enforce_daily_cap,
    force_reset_daily_baseline,
    maybe_reset_daily_breaker,
    profile_paused,
    saved_search_name,
    set_profile_paused,
    used_today,
)

TODAY = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
YESTERDAY = (_dt.datetime.now(_dt.timezone.utc).date() - _dt.timedelta(days=1)).isoformat()


def make_theirstack(used_api_credits: int, active: bool = True, missing: bool = False):
    """Both search profiles (automation, cpg) get their own saved search and
    webhook, keyed by name/search_id so the two profiles don't collide."""
    ts = MagicMock()
    ts.credit_balance.return_value = {"api_credits": 200, "used_api_credits": used_api_credits}

    searches = {
        saved_search_name("automation"): None if missing else {"id": 101, "is_alert_active": active},
        saved_search_name("cpg"): None if missing else {"id": 102, "is_alert_active": active},
    }
    webhooks = {101: None if missing else {"id": 201, "is_active": active},
                102: None if missing else {"id": 202, "is_active": active}}
    ts.find_saved_search.side_effect = lambda name: searches.get(name)
    ts.find_webhook_for_search.side_effect = lambda search_id: webhooks.get(search_id)
    return ts


def test_under_threshold_does_not_trip():
    ts = make_theirstack(used_api_credits=100)
    db = MagicMock()
    result = check_and_enforce_budget(ts, db, monthly_credit_budget=200)
    assert result["tripped"] is False
    ts.set_saved_search_active.assert_not_called()
    ts.set_webhook_active.assert_not_called()
    db.log_event.assert_not_called()


def test_at_90_percent_trips_and_disables_both_profiles():
    ts = make_theirstack(used_api_credits=180, active=True)
    db = MagicMock()
    result = check_and_enforce_budget(ts, db, monthly_credit_budget=200)
    assert result["tripped"] is True
    assert ts.set_saved_search_active.call_count == 2
    ts.set_saved_search_active.assert_any_call(101, False)
    ts.set_saved_search_active.assert_any_call(102, False)
    assert ts.set_webhook_active.call_count == 2
    ts.set_webhook_active.assert_any_call(201, False)
    ts.set_webhook_active.assert_any_call(202, False)
    db.log_event.assert_called_once()
    assert db.log_event.call_args.kwargs["event"] == "circuit_breaker_tripped"


def test_already_disabled_is_not_disabled_again():
    # avoid a redundant API call once the breaker has already tripped once
    ts = make_theirstack(used_api_credits=195, active=False)
    db = MagicMock()
    check_and_enforce_budget(ts, db, monthly_credit_budget=200)
    ts.set_saved_search_active.assert_not_called()
    ts.set_webhook_active.assert_not_called()


def test_missing_saved_search_or_webhook_does_not_raise():
    ts = make_theirstack(used_api_credits=200, missing=True)
    db = MagicMock()
    result = check_and_enforce_budget(ts, db, monthly_credit_budget=200)
    assert result["tripped"] is True


# ---- set_profile_paused / profile_paused: per-profile, not both at once ----


def test_set_profile_paused_only_touches_the_one_profile():
    ts = make_theirstack(used_api_credits=0, active=True)
    assert set_profile_paused(ts, "cpg", True) is True
    ts.set_saved_search_active.assert_called_once_with(102, False)
    ts.set_webhook_active.assert_called_once_with(202, False)


def test_set_profile_paused_already_paused_is_not_paused_again():
    ts = make_theirstack(used_api_credits=0, active=False)
    set_profile_paused(ts, "automation", True)
    ts.set_saved_search_active.assert_not_called()
    ts.set_webhook_active.assert_not_called()


def test_set_profile_paused_resume():
    ts = make_theirstack(used_api_credits=0, active=False)
    assert set_profile_paused(ts, "automation", False) is True
    ts.set_saved_search_active.assert_called_once_with(101, True)
    ts.set_webhook_active.assert_called_once_with(201, True)


def test_set_profile_paused_missing_search_returns_false():
    ts = make_theirstack(used_api_credits=0, missing=True)
    assert set_profile_paused(ts, "cpg", True) is False
    ts.set_saved_search_active.assert_not_called()


def test_profile_paused_reads_current_state():
    ts = make_theirstack(used_api_credits=0, active=True)
    assert profile_paused(ts, "automation") is False
    ts2 = make_theirstack(used_api_credits=0, active=False)
    assert profile_paused(ts2, "cpg") is True


def test_profile_paused_missing_search_returns_none():
    ts = make_theirstack(used_api_credits=0, missing=True)
    assert profile_paused(ts, "automation") is None


def test_profile_paused_theirstack_failure_returns_none_not_raise():
    # Found live: a 429 from TheirStack propagated straight through and
    # crashed /api/settings entirely — this is a status pill riding along on
    # a route loaded on every page view, it must degrade, not take the
    # dashboard down.
    ts = MagicMock()
    ts.find_saved_search.side_effect = RuntimeError("429 Too Many Requests")
    assert profile_paused(ts, "automation") is None


# ---- used_today: anchored to TheirStack's real balance, not our own log ----


def make_db_with_settings(settings):
    """update_settings mutates the same dict the test holds a reference to,
    so a baseline reset is visible on the next call — mirrors FakeDB's real
    behavior without pulling in the whole ingest test fixture."""
    db = MagicMock()
    db.update_settings.side_effect = lambda fields: settings.update(fields) or settings
    return db


def test_used_today_first_call_sets_baseline_and_returns_zero():
    ts = make_theirstack(used_api_credits=50)
    settings = {}  # no baseline yet
    db = make_db_with_settings(settings)
    assert used_today(ts, db, settings) == 0
    assert settings["credit_balance_baseline"] == 50
    assert settings["credit_balance_baseline_date"] == TODAY


def test_used_today_diffs_against_existing_same_day_baseline():
    ts = make_theirstack(used_api_credits=65)
    settings = {"credit_balance_baseline": 50, "credit_balance_baseline_date": TODAY}
    db = make_db_with_settings(settings)
    assert used_today(ts, db, settings) == 15
    db.update_settings.assert_not_called()  # baseline still valid, no reset needed


def test_used_today_resets_baseline_on_a_new_day():
    ts = make_theirstack(used_api_credits=65)
    settings = {"credit_balance_baseline": 50, "credit_balance_baseline_date": YESTERDAY}
    db = make_db_with_settings(settings)
    assert used_today(ts, db, settings) == 0  # new baseline just captured, nothing spent yet today
    assert settings["credit_balance_baseline"] == 65
    assert settings["credit_balance_baseline_date"] == TODAY


def test_used_today_does_not_drift_from_our_own_event_log():
    # regression: summing our own `events` log drifted from TheirStack's
    # real balance (verified live — log said 31, TheirStack said 18) because
    # hand-signed test webhook payloads never touched TheirStack's billing
    # but looked identical to a real delivery in our own log. The real
    # balance can't have that problem.
    ts = make_theirstack(used_api_credits=18)
    settings = {"credit_balance_baseline": 0, "credit_balance_baseline_date": TODAY}
    db = make_db_with_settings(settings)
    assert used_today(ts, db, settings) == 18


# ---- daily cap: a throttle, independent of the monthly budget ----


def test_daily_cap_none_never_trips():
    ts = make_theirstack(used_api_credits=999)
    settings = {"credit_balance_baseline": 0, "credit_balance_baseline_date": TODAY}
    db = make_db_with_settings(settings)
    result = check_and_enforce_daily_cap(ts, db, None, settings)
    assert result["tripped"] is False
    ts.set_saved_search_active.assert_not_called()


def test_daily_cap_zero_never_trips():
    ts = make_theirstack(used_api_credits=999)
    settings = {"credit_balance_baseline": 0, "credit_balance_baseline_date": TODAY}
    db = make_db_with_settings(settings)
    result = check_and_enforce_daily_cap(ts, db, 0, settings)
    assert result["tripped"] is False


def test_daily_cap_under_threshold_does_not_trip():
    ts = make_theirstack(used_api_credits=50)
    settings = {"credit_balance_baseline": 0, "credit_balance_baseline_date": TODAY}
    db = make_db_with_settings(settings)
    result = check_and_enforce_daily_cap(ts, db, 100, settings)
    assert result["tripped"] is False
    ts.set_saved_search_active.assert_not_called()


def test_daily_cap_at_threshold_trips_and_disables_both_profiles():
    ts = make_theirstack(used_api_credits=100, active=True)
    settings = {"credit_balance_baseline": 0, "credit_balance_baseline_date": TODAY}
    db = make_db_with_settings(settings)
    result = check_and_enforce_daily_cap(ts, db, 100, settings)
    assert result["tripped"] is True
    assert ts.set_saved_search_active.call_count == 2
    assert ts.set_webhook_active.call_count == 2
    db.log_event.assert_called_once()
    assert db.log_event.call_args.kwargs["event"] == "daily_cap_tripped"


def test_daily_cap_first_check_of_the_day_never_trips_even_if_high():
    # baseline gets set to the current total on the first check — there's no
    # way to know how much of a pre-existing balance belongs to "today"
    # without a reference point, so the first call always nets to zero.
    ts = make_theirstack(used_api_credits=500)
    settings = {}
    db = make_db_with_settings(settings)
    result = check_and_enforce_daily_cap(ts, db, 100, settings)
    assert result["tripped"] is False
    assert result["used_today"] == 0


# ---- maybe_reset_daily_breaker ----


def test_reset_noop_when_nothing_ever_tripped():
    ts = make_theirstack(used_api_credits=0, active=True)
    db = MagicMock()
    db.get_latest_event.return_value = None
    result = maybe_reset_daily_breaker(ts, db)
    assert result["reset"] is False
    ts.set_saved_search_active.assert_not_called()


def test_reset_noop_when_monthly_breaker_is_the_latest_trip():
    ts = make_theirstack(used_api_credits=0, active=False)
    db = MagicMock()
    db.get_latest_event.return_value = {
        "event": "circuit_breaker_tripped", "created_at": f"{YESTERDAY}T12:00:00+00:00",
    }
    result = maybe_reset_daily_breaker(ts, db)
    assert result["reset"] is False
    assert "daily throttle" in result["reason"]
    ts.set_saved_search_active.assert_not_called()
    db.log_event.assert_not_called()


def test_reset_noop_when_daily_cap_tripped_earlier_today():
    ts = make_theirstack(used_api_credits=0, active=False)
    db = MagicMock()
    db.get_latest_event.return_value = {
        "event": "daily_cap_tripped", "created_at": f"{TODAY}T01:00:00+00:00",
    }
    result = maybe_reset_daily_breaker(ts, db)
    assert result["reset"] is False
    assert "same UTC day" in result["reason"]
    ts.set_saved_search_active.assert_not_called()


def test_reset_re_enables_when_daily_cap_tripped_a_prior_day():
    ts = make_theirstack(used_api_credits=0, active=False)
    db = MagicMock()
    db.get_latest_event.return_value = {
        "event": "daily_cap_tripped", "created_at": f"{YESTERDAY}T23:03:00+00:00",
    }
    result = maybe_reset_daily_breaker(ts, db)
    assert result["reset"] is True
    assert ts.set_saved_search_active.call_count == 2
    assert ts.set_webhook_active.call_count == 2
    ts.set_saved_search_active.assert_any_call(101, True)
    ts.set_webhook_active.assert_any_call(201, True)
    db.log_event.assert_called_once()
    assert db.log_event.call_args.kwargs["event"] == "daily_cap_reset"


def test_reset_already_enabled_is_not_toggled_again():
    ts = make_theirstack(used_api_credits=0, active=True)
    db = MagicMock()
    db.get_latest_event.return_value = {
        "event": "daily_cap_tripped", "created_at": f"{YESTERDAY}T23:03:00+00:00",
    }
    result = maybe_reset_daily_breaker(ts, db)
    assert result["reset"] is True
    ts.set_saved_search_active.assert_not_called()
    ts.set_webhook_active.assert_not_called()


# ---- force_reset_daily_baseline: manual same-day reset, doesn't touch profiles ----


def test_force_reset_sets_baseline_to_current_usage():
    ts = make_theirstack(used_api_credits=536, active=False)
    settings = {"credit_balance_baseline": 0, "credit_balance_baseline_date": YESTERDAY}
    db = make_db_with_settings(settings)
    result = force_reset_daily_baseline(ts, db)
    assert result == {"reset": True, "new_baseline": 536}
    assert settings["credit_balance_baseline"] == 536
    assert settings["credit_balance_baseline_date"] == TODAY


def test_force_reset_does_not_touch_profile_active_state():
    # cpg paused on purpose (not by the breaker) must stay paused — a
    # same-day reset is about the spend number, not which profiles run.
    ts = make_theirstack(used_api_credits=536, active=True)
    settings = {}
    db = make_db_with_settings(settings)
    force_reset_daily_baseline(ts, db)
    ts.set_saved_search_active.assert_not_called()
    ts.set_webhook_active.assert_not_called()


def test_force_reset_logs_a_manual_daily_cap_reset_event():
    ts = make_theirstack(used_api_credits=536, active=True)
    settings = {}
    db = make_db_with_settings(settings)
    force_reset_daily_baseline(ts, db)
    db.log_event.assert_called_once()
    kwargs = db.log_event.call_args.kwargs
    assert kwargs["event"] == "daily_cap_reset"
    assert kwargs["payload"]["manual"] is True


def test_used_today_reads_zero_immediately_after_force_reset():
    ts = make_theirstack(used_api_credits=536, active=True)
    settings = {}
    db = make_db_with_settings(settings)
    force_reset_daily_baseline(ts, db)
    assert used_today(ts, db, settings) == 0
