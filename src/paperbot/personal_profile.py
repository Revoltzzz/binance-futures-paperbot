"""User-controlled personal trading profile helpers.

The profile is intentionally separate from worker account storage. It never writes
paper-session state or submits orders.
"""
from __future__ import annotations

DEFAULT_PROFILE = {
    "risk_mode": "Balanced",
    "preferred_symbol": "SOLUSDT",
    "trading_style": "scalping",
    "risk_per_trade_pct": 0.5,
    "max_daily_loss_pct": 2.0,
    "minimum_risk_reward": 1.5,
    "reference_capital_usdt": 100.0,
    "minimum_setup_score": 4.0,
}


def normalise_profile(raw: dict | None) -> dict:
    """Return validated settings, falling back field-by-field to safe defaults."""
    profile = dict(DEFAULT_PROFILE)
    if not isinstance(raw, dict):
        return profile
    allowed = {
        "risk_mode": {"Conservative", "Balanced", "Aggressive"},
        "preferred_symbol": {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"},
        "trading_style": {"scalping", "intraday"},
    }
    for key, choices in allowed.items():
        if raw.get(key) in choices:
            profile[key] = raw[key]
    bounds = {
        "risk_per_trade_pct": (0.1, 1.0),
        "max_daily_loss_pct": (0.5, 5.0),
        "minimum_risk_reward": (1.0, 4.0),
        "reference_capital_usdt": (10.0, 100_000.0),
        "minimum_setup_score": (3.0, 6.0),
    }
    for key, (low, high) in bounds.items():
        try:
            value = float(raw.get(key, profile[key]))
        except (TypeError, ValueError):
            continue
        if low <= value <= high:
            profile[key] = value
    return profile


def calculate_position_size(
    capital_usdt: float,
    risk_pct: float,
    entry_price: float,
    stop_price: float,
) -> dict:
    """Estimate base-asset quantity from a USDT risk budget; no exchange sizing."""
    values = (capital_usdt, risk_pct, entry_price, stop_price)
    if any(not isinstance(v, (int, float)) or v <= 0 for v in values):
        raise ValueError("Capital, risk, entry and stop must all be positive numbers")
    if entry_price == stop_price:
        raise ValueError("Entry and stop must be different")
    risk_amount = capital_usdt * risk_pct / 100.0
    stop_distance = abs(entry_price - stop_price)
    quantity = risk_amount / stop_distance
    return {
        "risk_amount_usdt": round(risk_amount, 4),
        "stop_distance": round(stop_distance, 8),
        "estimated_base_quantity": round(quantity, 8),
        "notional_usdt": round(quantity * entry_price, 4),
    }
