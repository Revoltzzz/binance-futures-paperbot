from dataclasses import dataclass

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


def run_backtest(
    candles: list[Candle],
    starting_balance: float = 1000.0,
    strategy: EmaCrossStrategy | None = None,
) -> BacktestResult:
    """Backtest signals at candle close and execute them at the next candle open.

    This avoids the optimistic look-ahead bias caused by executing a signal at
    the same close that was needed to calculate it. Intrabar stop/target checks
    use each candle's high/low after any pending order is filled at its open.
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

    for candle in candles:
        # A signal from the prior completed candle becomes actionable at this
        # candle's open, never at the close used to produce that signal.
        if pending_signal is not None:
            if broker.position is not None and pending_signal != broker.position.side:
                broker.close_position(candle.open, "signal_reversal")
            if broker.position is None:
                broker.open_position(pending_signal, candle.open)
            pending_signal = None

        # Only positions that existed at the open are exposed to this candle's
        # high/low range. If both stop and target are crossed, PaperBroker uses
        # its conservative stop-first assumption.
        broker.on_candle(candle)
        history.append(candle)
        pending_signal = strategy.signal(history)

        equity = broker.equity(candle.close)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)

    if broker.position is not None and candles:
        broker.close_position(candles[-1].close, "end_of_data")

    ending = broker.balance
    # Include final exit fees and realized P&L in the ending drawdown reading.
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
