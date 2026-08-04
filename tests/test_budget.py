from unittest.mock import MagicMock

from sightline.budget import check_and_enforce_budget, check_and_enforce_daily_cap, saved_search_name


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


# ---- daily cap: a throttle, independent of the monthly budget ----


def test_daily_cap_none_never_trips():
    ts = make_theirstack(used_api_credits=10)
    db = MagicMock()
    db.credits_used_today.return_value = 999  # would trip if the cap were checked
    result = check_and_enforce_daily_cap(ts, db, None)
    assert result["tripped"] is False
    ts.set_saved_search_active.assert_not_called()


def test_daily_cap_zero_never_trips():
    ts = make_theirstack(used_api_credits=10)
    db = MagicMock()
    db.credits_used_today.return_value = 999
    result = check_and_enforce_daily_cap(ts, db, 0)
    assert result["tripped"] is False


def test_daily_cap_under_threshold_does_not_trip():
    ts = make_theirstack(used_api_credits=10)
    db = MagicMock()
    db.credits_used_today.return_value = 50
    result = check_and_enforce_daily_cap(ts, db, 100)
    assert result["tripped"] is False
    ts.set_saved_search_active.assert_not_called()


def test_daily_cap_at_threshold_trips_and_disables_both_profiles():
    ts = make_theirstack(used_api_credits=10, active=True)
    db = MagicMock()
    db.credits_used_today.return_value = 100
    result = check_and_enforce_daily_cap(ts, db, 100)
    assert result["tripped"] is True
    assert ts.set_saved_search_active.call_count == 2
    assert ts.set_webhook_active.call_count == 2
    db.log_event.assert_called_once()
    assert db.log_event.call_args.kwargs["event"] == "daily_cap_tripped"


def test_daily_cap_independent_of_monthly_budget():
    # low monthly usage, but the daily throttle still trips on its own
    ts = make_theirstack(used_api_credits=5, active=True)
    db = MagicMock()
    db.credits_used_today.return_value = 100
    result = check_and_enforce_daily_cap(ts, db, 100)
    assert result["tripped"] is True
