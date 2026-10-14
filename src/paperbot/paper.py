from .models import Candle, Position, Side, Trade
from .risk import RiskConfig, RiskManager


class PaperBroker:
    """Local-only broker. It never sends orders or contacts Binance."""
    def __init__(self, starting_balance: float = 1000.0, config: RiskConfig | None = None):
        if starting_balance <= 0:
            raise ValueError("starting_balance must be positive")
        self.balance = float(starting_balance)
        self.starting_balance = float(starting_balance)
        self.risk = RiskManager(config)
        self.position: Position | None = None
        self.trades: list[Trade] = []

    def open_position(self, side: Side, price: float) -> Position | None:
        if self.position is not None or price <= 0:
            return None
        cfg = self.risk.config
        stop = price * (1 - cfg.stop_loss_pct) if side == Side.LONG else price * (1 + cfg.stop_loss_pct)
        target = price * (1 + cfg.stop_loss_pct * cfg.reward_risk) if side == Side.LONG else price * (1 - cfg.stop_loss_pct * cfg.reward_risk)
        qty = self.risk.size_position(self.balance, price, stop)
        if qty <= 0:
            return None
        fee = qty * price * cfg.fee_rate
        if fee >= self.balance:
            return None
        self.balance -= fee
        self.position = Position(side, qty, price, stop, target, fee)
        return self.position

    def close_position(self, price: float, reason: str = "signal") -> Trade | None:
        pos = self.position
        if pos is None or price <= 0:
            return None
        gross = (price - pos.entry_price) * pos.quantity
        if pos.side == Side.SHORT:
            gross = -gross
        exit_fee = price * pos.quantity * self.risk.config.fee_rate
        self.balance += gross - exit_fee
        trade = Trade(pos.side, pos.entry_price, price, pos.quantity,
                      gross - pos.entry_fee - exit_fee, pos.entry_fee + exit_fee, reason)
        self.trades.append(trade)
        self.position = None
        return trade

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
        if self.position is None or mark_price is None:
            return self.balance
        pos = self.position
        unrealized = (mark_price - pos.entry_price) * pos.quantity
        if pos.side == Side.SHORT:
            unrealized = -unrealized
        return self.balance + unrealized
