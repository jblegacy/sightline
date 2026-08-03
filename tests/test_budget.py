from unittest.mock import MagicMock

from sightline.budget import check_and_enforce_budget


def make_theirstack(used_api_credits: int, saved_search=None, webhook=None):
    ts = MagicMock()
    ts.credit_balance.return_value = {"api_credits": 200, "used_api_credits": used_api_credits}
    ts.find_saved_search.return_value = saved_search
    ts.find_webhook.return_value = webhook
    return ts


def test_under_threshold_does_not_trip():
    ts = make_theirstack(used_api_credits=100)
    db = MagicMock()
    result = check_and_enforce_budget(ts, db, "https://example.com/webhook", monthly_credit_budget=200)
    assert result["tripped"] is False
    ts.set_saved_search_active.assert_not_called()
    ts.set_webhook_active.assert_not_called()
    db.log_event.assert_not_called()


def test_at_90_percent_trips_and_disables_both():
    ts = make_theirstack(
        used_api_credits=180,
        saved_search={"id": 61565, "is_alert_active": True},
        webhook={"id": 5343, "is_active": True},
    )
    db = MagicMock()
    result = check_and_enforce_budget(ts, db, "https://example.com/webhook", monthly_credit_budget=200)
    assert result["tripped"] is True
    ts.set_saved_search_active.assert_called_once_with(61565, False)
    ts.set_webhook_active.assert_called_once_with(5343, False)
    db.log_event.assert_called_once()
    assert db.log_event.call_args.kwargs["event"] == "circuit_breaker_tripped"


def test_already_disabled_is_not_disabled_again():
    # avoid a redundant API call once the breaker has already tripped once
    ts = make_theirstack(
        used_api_credits=195,
        saved_search={"id": 1, "is_alert_active": False},
        webhook={"id": 2, "is_active": False},
    )
    db = MagicMock()
    check_and_enforce_budget(ts, db, "https://example.com/webhook", monthly_credit_budget=200)
    ts.set_saved_search_active.assert_not_called()
    ts.set_webhook_active.assert_not_called()


def test_missing_saved_search_or_webhook_does_not_raise():
    ts = make_theirstack(used_api_credits=200, saved_search=None, webhook=None)
    db = MagicMock()
    result = check_and_enforce_budget(ts, db, "https://example.com/webhook", monthly_credit_budget=200)
    assert result["tripped"] is True
