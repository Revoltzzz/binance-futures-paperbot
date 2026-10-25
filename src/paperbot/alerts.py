"""Deterministic paper-trading risk alerts; this module never places orders.

The alert evaluator is intentionally side-effect free so dashboards, workers,
and tests can consume the same rules without coupling alerts to execution.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


def evaluate_risk_alerts(
    *,
    current_equity: float,
    day_start_equity: float,
    peak_equity: float,
    candle_age_seconds: float,
    max_daily_loss_pct: float = 0.03,
    max_drawdown_pct: float = 0.10,
    max_candle_age_seconds: float = 900.0,
    volatility_pct: float | None = None,
    max_volatility_pct: float = 0.03,
) -> list[dict[str, Any]]:
    """Return active risk alerts from a single paper-account snapshot.

    Percentages are decimal fractions (0.03 = 3%). Invalid/non-finite inputs
    raise ValueError rather than silently suppressing a risk warning.
    """
    values = {
        "current_equity": current_equity,
        "day_start_equity": day_start_equity,
        "peak_equity": peak_equity,
        "candle_age_seconds": candle_age_seconds,
        "max_daily_loss_pct": max_daily_loss_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "max_candle_age_seconds": max_candle_age_seconds,
        "max_volatility_pct": max_volatility_pct,
    }
    if volatility_pct is not None:
        values["volatility_pct"] = volatility_pct
    for name, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be numeric")
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")

    if day_start_equity <= 0 or peak_equity <= 0:
        raise ValueError("reference equity values must be positive")
    if candle_age_seconds < 0 or max_candle_age_seconds <= 0:
        raise ValueError("candle age must be non-negative and its limit positive")
    if not 0 < max_daily_loss_pct < 1 or not 0 < max_drawdown_pct < 1:
        raise ValueError("loss and drawdown limits must be between 0 and 1")
    if max_volatility_pct <= 0:
        raise ValueError("max_volatility_pct must be positive")
    if volatility_pct is not None and volatility_pct < 0:
        raise ValueError("volatility_pct must be non-negative")

    alerts: list[dict[str, Any]] = []

    def add(code: str, severity: str, message: str, observed: float, limit: float) -> None:
        alerts.append({
            "code": code,
            "severity": severity,
            "message": message,
            "observed": observed,
            "limit": limit,
            "action": "review_and_pause_new_entries",
        })

    daily_loss = max(0.0, (day_start_equity - current_equity) / day_start_equity)
    drawdown = max(0.0, (peak_equity - current_equity) / peak_equity)
    if daily_loss >= max_daily_loss_pct:
        add("DAILY_LOSS_LIMIT", "critical", "Daily loss limit reached", daily_loss, max_daily_loss_pct)
    if drawdown >= max_drawdown_pct:
        add("DRAWDOWN_LIMIT", "critical", "Peak-to-current drawdown limit reached", drawdown, max_drawdown_pct)
    if candle_age_seconds > max_candle_age_seconds:
        add("STALE_MARKET_DATA", "high", "Latest candle is too old for safe decisions", candle_age_seconds, max_candle_age_seconds)
    if volatility_pct is not None and volatility_pct >= max_volatility_pct:
        add("VOLATILITY_SPIKE", "high", "Volatility exceeds configured threshold", volatility_pct, max_volatility_pct)
    return alerts
