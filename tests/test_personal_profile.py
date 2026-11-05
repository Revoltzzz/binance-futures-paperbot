from paperbot.personal_profile import (
    DEFAULT_PROFILE, calculate_position_size, normalise_profile,
)


def test_balanced_personal_profile_defaults_to_sol_scalping():
    profile = normalise_profile(None)
    assert profile == DEFAULT_PROFILE
    assert profile["risk_mode"] == "Balanced"
    assert profile["preferred_symbol"] == "SOLUSDT"
    assert profile["trading_style"] == "scalping"


def test_profile_rejects_out_of_range_risk_values():
    profile = normalise_profile({"risk_per_trade_pct": 50, "preferred_symbol": "DOGEUSDT"})
    assert profile["risk_per_trade_pct"] == DEFAULT_PROFILE["risk_per_trade_pct"]
    assert profile["preferred_symbol"] == "SOLUSDT"


def test_position_size_respects_risk_budget():
    result = calculate_position_size(100, 0.5, 100, 98)
    assert result["risk_amount_usdt"] == 0.5
    assert result["estimated_base_quantity"] == 0.25
    assert result["notional_usdt"] == 25.0


def test_position_size_rejects_invalid_or_equal_prices():
    for args in [(0, 0.5, 100, 98), (100, 0.5, 100, 100)]:
        try:
            calculate_position_size(*args)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid sizing input should be rejected")
