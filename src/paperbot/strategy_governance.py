"""Conservative, paper-only strategy promotion review.

This module reviews closed-trade results; it never changes the active strategy,
submits orders, or treats news headlines as trade signals. Use only out-of-sample
paper/backtest trades after fees, slippage, and funding have been modelled.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any


def review_strategy_candidate(
    trades: Iterable[Mapping[str, Any]],
    *,
    starting_equity: float = 1000.0,
    min_trades: int = 30,
    min_profit_factor: float = 1.2,
    max_drawdown_pct: float = 0.10,
) -> dict[str, Any]:
    """Return transparent promotion criteria for a candidate strategy.

    Each trade must contain finite numeric net_pnl in account currency.
    A candidate passes only if the sample is large enough, net P&L is positive,
    profit factor clears the threshold, and peak-to-trough drawdown stays within
    the limit. This is a screening rule, not evidence of future profitability.
    """
    if isinstance(starting_equity, bool) or not isinstance(starting_equity, (int, float)):
        raise ValueError("starting_equity must be numeric")
    if not math.isfinite(starting_equity) or starting_equity <= 0:
        raise ValueError("starting_equity must be finite and positive")
    if isinstance(min_trades, bool) or not isinstance(min_trades, int) or min_trades < 1:
        raise ValueError("min_trades must be a positive integer")
    if isinstance(min_profit_factor, bool) or not isinstance(min_profit_factor, (int, float)):
        raise ValueError("min_profit_factor must be numeric")
    if not math.isfinite(min_profit_factor) or min_profit_factor <= 0:
        raise ValueError("min_profit_factor must be finite and positive")
    if isinstance(max_drawdown_pct, bool) or not isinstance(max_drawdown_pct, (int, float)):
        raise ValueError("max_drawdown_pct must be numeric")
    if not math.isfinite(max_drawdown_pct) or not 0 < max_drawdown_pct < 1:
        raise ValueError("max_drawdown_pct must be between 0 and 1")

    values: list[float] = []
    for trade in trades:
        if not isinstance(trade, Mapping) or "net_pnl" not in trade:
            raise ValueError("each trade must be a mapping with net_pnl")
        pnl = trade["net_pnl"]
        if isinstance(pnl, bool) or not isinstance(pnl, (int, float)) or not math.isfinite(pnl):
            raise ValueError("trade net_pnl must be a finite number")
        values.append(float(pnl))

    equity = float(starting_equity)
    peak = equity
    max_drawdown = 0.0
    for pnl in values:
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak if peak > 0 else 1.0)

    wins = sum(pnl for pnl in values if pnl > 0)
    losses = -sum(pnl for pnl in values if pnl < 0)
    profit_factor = wins / losses if losses > 0 else None
    net_pnl = sum(values)
    checks = {
        "minimum_sample": len(values) >= min_trades,
        "positive_net_pnl": net_pnl > 0,
        "minimum_profit_factor": profit_factor is not None and profit_factor >= min_profit_factor,
        "maximum_drawdown": max_drawdown <= max_drawdown_pct,
    }
    return {
        "sample_size": len(values),
        "minimum_trades_required": min_trades,
        "net_pnl": net_pnl,
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_drawdown * 100,
        "max_drawdown_limit_pct": max_drawdown_pct * 100,
        "checks": checks,
        "eligible_for_manual_review": all(checks.values()),
        "active_strategy_changed": False,
        "live_orders_enabled": False,
        "note": (
            "Eligible for manual review only; validate on untouched out-of-sample data. "
            "This result does not predict future performance."
        ),
    }
