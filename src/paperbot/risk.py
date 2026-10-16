from dataclasses import dataclass
import math


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float = 0.01
    stop_loss_pct: float = 0.01
    reward_risk: float = 2.0
    max_leverage: float = 2.0
    fee_rate: float = 0.0004
    max_daily_loss_pct: float = 0.03
    slippage_rate: float = 0.0002
    funding_rate_per_8h: float = 0.0001

    def __post_init__(self):
        values = (
            self.risk_per_trade, self.stop_loss_pct, self.reward_risk,
            self.max_leverage, self.fee_rate, self.max_daily_loss_pct,
            self.slippage_rate, self.funding_rate_per_8h,
        )
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
            raise ValueError("risk settings must be numeric values, not booleans")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("risk settings must be finite numbers")
        if not 0 < self.risk_per_trade <= 0.02:
            raise ValueError("risk_per_trade must be in (0, 0.02]")
        if not 0 < self.stop_loss_pct < 1:
            raise ValueError("stop_loss_pct must be in (0, 1)")
        if self.reward_risk <= 0 or self.max_leverage < 1:
            raise ValueError("reward_risk must be positive and leverage >= 1")
        if not 0 <= self.fee_rate < 0.1:
            raise ValueError("fee_rate must be in [0, 0.1)")
        if not 0 < self.max_daily_loss_pct < 1:
            raise ValueError("max_daily_loss_pct must be in (0, 1)")
        if not 0 <= self.slippage_rate < 0.1:
            raise ValueError("slippage_rate must be in [0, 0.1)")
        if not -0.1 < self.funding_rate_per_8h < 0.1:
            raise ValueError("funding_rate_per_8h must be between -0.1 and 0.1")


class RiskManager:
    def __init__(self, config: RiskConfig | None = None):
        self.config = config or RiskConfig()

    def size_position(self, equity: float, entry: float, stop: float) -> float:
        if not all(math.isfinite(value) for value in (equity, entry, stop)):
            return 0.0
        if equity <= 0 or entry <= 0 or stop <= 0:
            return 0.0
        distance = abs(entry - stop)
        if distance <= 0 or distance >= entry:
            return 0.0
        risk_budget = equity * self.config.risk_per_trade
        # Budget for stop-distance loss plus estimated entry and stop-exit fees.
        estimated_round_trip_cost_per_unit = (
            entry * (self.config.fee_rate + self.config.slippage_rate)
            + stop * (self.config.fee_rate + self.config.slippage_rate)
        )
        qty_by_risk = risk_budget / (distance + estimated_round_trip_cost_per_unit)
        qty_by_leverage = equity * self.config.max_leverage / entry
        return max(0.0, min(qty_by_risk, qty_by_leverage))

    def daily_loss_limit_hit(self, starting_equity: float, current_equity: float) -> bool:
        if not math.isfinite(starting_equity) or not math.isfinite(current_equity):
            return True
        if starting_equity <= 0:
            return True
        return (starting_equity - current_equity) / starting_equity >= self.config.max_daily_loss_pct
