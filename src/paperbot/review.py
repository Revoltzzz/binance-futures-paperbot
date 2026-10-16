"""Transparent post-trade diagnostics; this module never changes strategy parameters."""
from __future__ import annotations

import math
from typing import Iterable


def summarize_trades(trades: Iterable[dict], window: int = 20) -> dict:
    """Summarize the most recent closed trades without inferring future profitability."""
    if not isinstance(window, int) or isinstance(window, bool) or window < 1:
        raise ValueError("window must be a positive integer")
    rows = list(trades)[-window:]
    values = []
    for row in rows:
        try:
            pnl = float(row["Net P&L (USDT)"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("each trade must contain numeric Net P&L (USDT)") from exc
        if not math.isfinite(pnl):
            raise ValueError("trade P&L must be finite")
        values.append(pnl)
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    cumulative_pnl = 0.0
    peak_pnl = 0.0
    max_drawdown = 0.0
    for pnl in values:
        cumulative_pnl += pnl
        peak_pnl = max(peak_pnl, cumulative_pnl)
        max_drawdown = max(max_drawdown, peak_pnl - cumulative_pnl)

    net_pnl = sum(values)
    return {
        "sample_size": len(values),
        "window": window,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": (len(wins) / len(values) * 100) if values else None,
        "net_pnl": net_pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "expectancy_per_trade": (net_pnl / len(values)) if values else None,
        "profit_factor": (
            gross_profit / gross_loss if gross_loss > 0 else None
        ),
        "average_win": (sum(wins) / len(wins)) if wins else None,
        "average_loss": (sum(losses) / len(losses)) if losses else None,
        "max_drawdown_usdt": max_drawdown,
        "recovery_factor": net_pnl / max_drawdown if max_drawdown > 0 else None,
    }
