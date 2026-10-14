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
