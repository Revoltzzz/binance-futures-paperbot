import pytest
from paperbot.risk import RiskConfig, RiskManager


def test_size_position_respects_risk_budget():
    risk = RiskManager(RiskConfig(risk_per_trade=0.01, stop_loss_pct=0.02, max_leverage=2))
    qty = risk.size_position(1000, 100, 98)
    assert qty == pytest.approx(5)
    assert qty * 100 <= 2000


def test_daily_loss_limit():
    risk = RiskManager()
    assert not risk.daily_loss_limit_hit(1000, 971)
    assert risk.daily_loss_limit_hit(1000, 970)


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        RiskConfig(risk_per_trade=0.5)
