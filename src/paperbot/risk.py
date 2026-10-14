from dataclasses import dataclass


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float = 0.01
    stop_loss_pct: float = 0.01
    reward_risk: float = 2.0
    max_leverage: float = 2.0
    fee_rate: float = 0.0004
    max_daily_loss_pct: float = 0.03

    def __post_init__(self):
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


class RiskManager:
    def __init__(self, config: RiskConfig | None = None):
        self.config = config or RiskConfig()

    def size_position(self, equity: float, entry: float, stop: float) -> float:
        if equity <= 0 or entry <= 0 or stop <= 0:
            return 0.0
        distance = abs(entry - stop)
        if distance <= 0 or distance >= entry:
            return 0.0
        risk_budget = equity * self.config.risk_per_trade
        qty_by_risk = risk_budget / distance
        qty_by_leverage = equity * self.config.max_leverage / entry
        return max(0.0, min(qty_by_risk, qty_by_leverage))

    def daily_loss_limit_hit(self, starting_equity: float, current_equity: float) -> bool:
        if starting_equity <= 0:
            return True
        return (starting_equity - current_equity) / starting_equity >= self.config.max_daily_loss_pct
