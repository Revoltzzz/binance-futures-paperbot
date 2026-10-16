import pytest

from paperbot.review import summarize_trades


def _trade(pnl):
    return {"Net P&L (USDT)": pnl}


def test_trade_review_reports_recent_window_and_loss_adjusted_metrics():
    result = summarize_trades([_trade(10), _trade(-4), _trade(6)], window=2)
    assert result["sample_size"] == 2
    assert result["wins"] == 1
    assert result["losses"] == 1
    assert result["win_rate_pct"] == 50
    assert result["net_pnl"] == 2
    assert result["expectancy_per_trade"] == 1
    assert result["profit_factor"] == 1.5


def test_trade_review_handles_empty_history_and_validates_values():
    assert summarize_trades([])["sample_size"] == 0
    with pytest.raises(ValueError):
        summarize_trades([{"Net P&L (USDT)": float("nan")}])
    with pytest.raises(ValueError):
        summarize_trades([], window=0)

def test_trade_review_reports_realized_drawdown_and_recovery_factor():
    result = summarize_trades([_trade(10), _trade(-4), _trade(6), _trade(-15)])
    assert result["gross_profit"] == 16
    assert result["gross_loss"] == 19
    assert result["max_drawdown_usdt"] == 15
    assert result["recovery_factor"] == pytest.approx(-3 / 15)


def test_trade_review_empty_history_has_zero_drawdown_and_no_recovery_factor():
    result = summarize_trades([])
    assert result["max_drawdown_usdt"] == 0
    assert result["recovery_factor"] is None

