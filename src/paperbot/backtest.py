from dataclasses import dataclass
from datetime import datetime, timezone

from .models import Candle, Side
from .paper import PaperBroker
from .strategy import EmaCrossStrategy


@dataclass(frozen=True)
class BacktestResult:
    starting_balance: float
    ending_balance: float
    total_return_pct: float
    trades: int
    wins: int
    losses: int
    max_drawdown_pct: float


def _utc_session(timestamp: int):
    """Accept Unix timestamps in seconds or milliseconds and return UTC date."""
    seconds = timestamp / 1000 if timestamp > 10_000_000_000 else timestamp
    return datetime.fromtimestamp(seconds, tz=timezone.utc).date()


def run_backtest(
    candles: list[Candle],
    starting_balance: float = 1000.0,
    strategy: EmaCrossStrategy | None = None,
) -> BacktestResult:
    """Backtest close-generated signals at the next candle open.

    Uses UTC calendar-day loss limits. When the daily equity loss threshold is
    reached, an open position is closed at that candle's close and new entries
    are blocked until the next UTC day. This is a simplified paper simulation,
    not an exchange-grade risk engine.
    """
    if starting_balance <= 0:
        raise ValueError("starting_balance must be positive")
    if any(current.timestamp <= previous.timestamp
           for previous, current in zip(candles, candles[1:])):
        raise ValueError("candles must have strictly increasing timestamps")

    strategy = strategy or EmaCrossStrategy()
    broker = PaperBroker(starting_balance)
    history: list[Candle] = []
    pending_signal: Side | None = None
    peak = float(starting_balance)
    max_dd = 0.0
    session = None
    session_start_equity = float(starting_balance)
    daily_halted = False

    for candle in candles:
        candle_session = _utc_session(candle.timestamp)
        if candle_session != session:
            session = candle_session
            session_start_equity = broker.equity(candle.open)
            daily_halted = False

        if pending_signal is not None and not daily_halted:
            if broker.position is not None and pending_signal != broker.position.side:
                broker.close_position(candle.open, "signal_reversal")
            if broker.position is None:
                broker.open_position(pending_signal, candle.open)
        pending_signal = None

        broker.on_candle(candle)
        history.append(candle)

        equity = broker.equity(candle.close)
        if broker.risk.daily_loss_limit_hit(session_start_equity, equity):
            if broker.position is not None:
                broker.close_position(candle.close, "daily_loss_limit")
                equity = broker.balance
            daily_halted = True

        if not daily_halted:
            pending_signal = strategy.signal(history)

        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)

    if broker.position is not None and candles:
        broker.close_position(candles[-1].close, "end_of_data")

    ending = broker.balance
    peak = max(peak, ending)
    if peak > 0:
        max_dd = max(max_dd, (peak - ending) / peak)
    wins = sum(trade.pnl > 0 for trade in broker.trades)
    losses = sum(trade.pnl <= 0 for trade in broker.trades)
    return BacktestResult(
        starting_balance,
        ending,
        (ending / starting_balance - 1) * 100,
        len(broker.trades),
        wins,
        losses,
        max_dd * 100,
    )
