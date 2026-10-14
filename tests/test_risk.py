import pytest
from paperbot.risk import RiskConfig, RiskManager


def test_size_position_respects_risk_budget_including_estimated_fees():
    config = RiskConfig(risk_per_trade=0.01, stop_loss_pct=0.02, max_leverage=2)
    risk = RiskManager(config)
    equity, entry, stop = 1000, 100, 98
    qty = risk.size_position(equity, entry, stop)
    estimated_loss_at_stop = qty * (
        abs(entry - stop) + entry * config.fee_rate + stop * config.fee_rate
    )
    assert 0 < qty < 5
    assert estimated_loss_at_stop <= equity * config.risk_per_trade
    assert qty * entry <= equity * config.max_leverage


def test_position_sizing_rejects_non_finite_inputs():
    risk = RiskManager()
    assert risk.size_position(float("nan"), 100, 99) == 0
    assert risk.size_position(1000, float("inf"), 99) == 0


def test_risk_config_rejects_non_finite_values():
    with pytest.raises(ValueError, match="finite"):
        RiskConfig(reward_risk=float("nan"))
    with pytest.raises(ValueError, match="finite"):
        RiskConfig(max_leverage=float("inf"))


def test_daily_loss_limit():
    risk = RiskManager()
    assert not risk.daily_loss_limit_hit(1000, 971)
    assert risk.daily_loss_limit_hit(1000, 970)


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        RiskConfig(risk_per_trade=0.5)
