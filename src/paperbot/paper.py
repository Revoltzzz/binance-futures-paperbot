import math

from .models import Candle, Position, Side, Trade
from .risk import RiskConfig, RiskManager


class PaperBroker:
    """Local-only broker. It never sends orders or contacts Binance."""
    def __init__(self, starting_balance: float = 1000.0, config: RiskConfig | None = None):
        if not math.isfinite(starting_balance) or starting_balance <= 0:
            raise ValueError("starting_balance must be a finite positive number")
        self.balance = float(starting_balance)
        self.starting_balance = float(starting_balance)
        self.risk = RiskManager(config)
        self.position: Position | None = None
        self.trades: list[Trade] = []

    def open_position(self, side: Side, price: float) -> Position | None:
        if self.position is not None or not math.isfinite(price) or price <= 0:
            return None
        cfg = self.risk.config
        execution_price = price * (1 + cfg.slippage_rate) if side == Side.LONG else price * (1 - cfg.slippage_rate)
        stop = execution_price * (1 - cfg.stop_loss_pct) if side == Side.LONG else execution_price * (1 + cfg.stop_loss_pct)
        target = execution_price * (1 + cfg.stop_loss_pct * cfg.reward_risk) if side == Side.LONG else execution_price * (1 - cfg.stop_loss_pct * cfg.reward_risk)
        qty = self.risk.size_position(self.balance, execution_price, stop)
        if qty <= 0:
            return None
        fee = qty * execution_price * cfg.fee_rate
        if fee >= self.balance:
            return None
        self.balance -= fee
        self.position = Position(side, qty, execution_price, stop, target, fee)
        return self.position

    def close_position(self, price: float, reason: str = "signal") -> Trade | None:
        pos = self.position
        if pos is None or not math.isfinite(price) or price <= 0:
            return None
        cfg = self.risk.config
        execution_price = price * (1 - cfg.slippage_rate) if pos.side == Side.LONG else price * (1 + cfg.slippage_rate)
        gross = (execution_price - pos.entry_price) * pos.quantity
        if pos.side == Side.SHORT:
            gross = -gross
        exit_fee = execution_price * pos.quantity * cfg.fee_rate
        self.balance += gross - exit_fee
        trade = Trade(pos.side, pos.entry_price, execution_price, pos.quantity,
                      gross - pos.entry_fee - exit_fee - pos.funding_cost,
                      pos.entry_fee + exit_fee, reason, pos.funding_cost)
        self.trades.append(trade)
        self.position = None
        return trade

    def apply_funding(self, mark_price: float, funding_rate: float | None = None) -> float:
        """Apply one funding event; positive rates charge longs and credit shorts."""
        pos = self.position
        if pos is None or not math.isfinite(mark_price) or mark_price <= 0:
            return 0.0
        rate = self.risk.config.funding_rate_per_8h if funding_rate is None else funding_rate
        if not math.isfinite(rate) or not -0.1 < rate < 0.1:
            raise ValueError("funding_rate must be finite and between -0.1 and 0.1")
        direction = 1.0 if pos.side == Side.LONG else -1.0
        cost = mark_price * pos.quantity * rate * direction
        self.balance -= cost
        pos.funding_cost += cost
        return cost

    def on_candle(self, candle: Candle) -> Trade | None:
        pos = self.position
        if pos is None:
            return None
        # Conservative if both levels cross in one candle: assume stop first.
        if pos.side == Side.LONG:
            if candle.low <= pos.stop_price:
                return self.close_position(pos.stop_price, "stop_loss")
            if candle.high >= pos.take_profit_price:
                return self.close_position(pos.take_profit_price, "take_profit")
        else:
            if candle.high >= pos.stop_price:
                return self.close_position(pos.stop_price, "stop_loss")
            if candle.low <= pos.take_profit_price:
                return self.close_position(pos.take_profit_price, "take_profit")
        return None

    def equity(self, mark_price: float | None = None) -> float:
        if mark_price is not None and (not math.isfinite(mark_price) or mark_price <= 0):
            raise ValueError("mark_price must be a finite positive number")
        if self.position is None or mark_price is None:
            return self.balance
        pos = self.position
        cfg = self.risk.config
        exit_mark = mark_price * (1 - cfg.slippage_rate) if pos.side == Side.LONG else mark_price * (1 + cfg.slippage_rate)
        unrealized = (exit_mark - pos.entry_price) * pos.quantity
        if pos.side == Side.SHORT:
            unrealized = -unrealized
        estimated_exit_fee = exit_mark * pos.quantity * cfg.fee_rate
        return self.balance + unrealized - estimated_exit_fee
