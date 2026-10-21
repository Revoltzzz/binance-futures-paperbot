import pytest

from paperbot.strategy_governance import review_strategy_candidate


def test_candidate_can_be_eligible_only_after_all_gates_pass():
    trades = [{"net_pnl": 10.0} for _ in range(24)] + [{"net_pnl": -5.0} for _ in range(6)]
    result = review_strategy_candidate(trades, starting_equity=1000, min_trades=30)
    assert result["sample_size"] == 30
    assert result["checks"] == {
        "minimum_sample": True,
        "positive_net_pnl": True,
        "minimum_profit_factor": True,
        "maximum_drawdown": True,
    }
    assert result["eligible_for_manual_review"] is True
    assert result["active_strategy_changed"] is False
    assert result["live_orders_enabled"] is False


def test_small_or_losing_sample_is_not_promoted():
    result = review_strategy_candidate([{"net_pnl": 2.0}, {"net_pnl": -4.0}], min_trades=30)
    assert result["eligible_for_manual_review"] is False
    assert result["checks"]["minimum_sample"] is False
    assert result["checks"]["positive_net_pnl"] is False


def test_drawdown_gate_blocks_a_deep_loss_even_if_net_positive():
    trades = [{"net_pnl": -150.0}, {"net_pnl": 200.0}] + [{"net_pnl": 1.0} for _ in range(28)]
    result = review_strategy_candidate(trades, starting_equity=1000, min_trades=30)
    assert result["net_pnl"] > 0
    assert result["checks"]["maximum_drawdown"] is False
    assert result["eligible_for_manual_review"] is False


@pytest.mark.parametrize("trades", [
    [{"net_pnl": float("nan")}],
    [{"net_pnl": float("inf")}],
    [{"pnl": 3}],
])
def test_invalid_trade_rows_fail_closed(trades):
    with pytest.raises(ValueError):
        review_strategy_candidate(trades)


def test_invalid_review_limits_are_rejected():
    with pytest.raises(ValueError):
        review_strategy_candidate([], min_trades=0)
    with pytest.raises(ValueError):
        review_strategy_candidate([], max_drawdown_pct=1)
    with pytest.raises(ValueError):
        review_strategy_candidate([], starting_equity=float("nan"))
