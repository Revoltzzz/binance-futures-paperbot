from dataclasses import dataclass
from .models import Candle
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


def run_backtest(candles: list[Candle], starting_balance: float = 1000.0,
                 strategy: EmaCrossStrategy | None = None) -> BacktestResult:
    strategy = strategy or EmaCrossStrategy()
    broker = PaperBroker(starting_balance)
    history: list[Candle] = []
    peak = starting_balance
    max_dd = 0.0
    for candle in candles:
        broker.on_candle(candle)
        history.append(candle)
        signal = strategy.signal(history)
        if broker.position is not None:
            if signal is not None and signal != broker.position.side:
                broker.close_position(candle.close, "signal_reversal")
        elif signal is not None:
            broker.open_position(signal, candle.close)
        equity = broker.equity(candle.close)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
    if broker.position is not None and candles:
        broker.close_position(candles[-1].close, "end_of_data")
    ending = broker.balance
    wins = sum(t.pnl > 0 for t in broker.trades)
    losses = sum(t.pnl <= 0 for t in broker.trades)
    return BacktestResult(starting_balance, ending, (ending / starting_balance - 1) * 100,
                          len(broker.trades), wins, losses, max_dd * 100)
