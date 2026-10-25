import pytest

from paperbot.alerts import evaluate_risk_alerts


def test_no_alerts_for_healthy_paper_snapshot():
    assert evaluate_risk_alerts(
        current_equity=1000,
        day_start_equity=1000,
        peak_equity=1050,
        candle_age_seconds=60,
        volatility_pct=0.01,
    ) == []


def test_daily_loss_and_drawdown_limits_emit_critical_alerts():
    alerts = evaluate_risk_alerts(
        current_equity=960,
        day_start_equity=1000,
        peak_equity=1100,
        candle_age_seconds=60,
    )
    codes = {alert["code"] for alert in alerts}
    assert "DAILY_LOSS_LIMIT" in codes
    assert "DRAWDOWN_LIMIT" in codes
    assert all(alert["severity"] == "critical" for alert in alerts)


def test_stale_data_and_volatility_spike_are_visible():
    alerts = evaluate_risk_alerts(
        current_equity=1000,
        day_start_equity=1000,
        peak_equity=1000,
        candle_age_seconds=901,
        volatility_pct=0.04,
    )
    assert {alert["code"] for alert in alerts} == {
        "STALE_MARKET_DATA", "VOLATILITY_SPIKE"
    }
    assert all(alert["action"] == "review_and_pause_new_entries" for alert in alerts)


@pytest.mark.parametrize("kwargs", [
    {"current_equity": float("nan")},
    {"day_start_equity": 0},
    {"peak_equity": -1},
    {"candle_age_seconds": -1},
    {"max_daily_loss_pct": 1},
    {"volatility_pct": -0.1},
])
def test_invalid_snapshots_fail_closed(kwargs):
    defaults = dict(
        current_equity=1000,
        day_start_equity=1000,
        peak_equity=1000,
        candle_age_seconds=60,
    )
    defaults.update(kwargs)
    with pytest.raises(ValueError):
        evaluate_risk_alerts(**defaults)


def test_alerts_do_not_claim_to_execute_or_modify_strategy():
    alerts = evaluate_risk_alerts(
        current_equity=900,
        day_start_equity=1000,
        peak_equity=1000,
        candle_age_seconds=60,
    )
    assert alerts
    assert all("action" in alert for alert in alerts)
    assert all("order" not in alert for alert in alerts)
