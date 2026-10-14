from dataclasses import dataclass
from enum import Enum


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True)
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self):
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC prices must be positive")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be the highest OHLC value")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be the lowest OHLC value")


@dataclass
class Position:
    side: Side
    quantity: float
    entry_price: float
    stop_price: float
    take_profit_price: float
    entry_fee: float = 0.0


@dataclass(frozen=True)
class Trade:
    side: Side
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    fees: float
    reason: str
