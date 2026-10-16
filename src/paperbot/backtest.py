from dataclasses import dataclass
import math
from datetime import datetime, timezone

from .models import Candle, Side, Trade
from .paper import PaperBroker
from .risk import RiskConfig
from .strategy import EmaCrossStrategy


@dataclass(frozen=True)
class TradeJournalEntry:
    entry_time_utc: str
    exit_time_utc: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    gross_pnl: float
    fees: float
    funding_cost: float
    net_pnl: float
    exit_reason: str
    balance_after: float


@dataclass(frozen=True)
class RiskJournalEntry:
    time_utc: str
    event: str
    marked_equity: float
    balance_after: float
    message: str


@dataclass(frozen=True)
class BacktestResult:
    starting_balance: float
    ending_balance: float
    total_return_pct: float
    trades: int
    wins: int
    losses: int
    max_drawdown_pct: float
    trade_history: tuple[TradeJournalEntry, ...] = ()
    risk_events: tuple[RiskJournalEntry, ...] = ()

    @property
    def positive_net_pnl(self) -> float:
        """Sum of positive net trade P&L after modeled costs."""
        return sum(max(0.0, trade.net_pnl) for trade in self.trade_history)

    @property
    def negative_net_pnl(self) -> float:
        """Absolute sum of negative net trade P&L after modeled costs."""
        return sum(abs(min(0.0, trade.net_pnl)) for trade in self.trade_history)

    @property
    def profit_factor(self) -> float | None:
        """Positive P&L divided by absolute negative P&L; None if no losses occurred."""
        loss = self.negative_net_pnl
        return self.positive_net_pnl / loss if loss > 0 else None

    @property
    def expectancy_per_trade(self) -> float:
        """Mean net P&L per closed trade, or zero when there are no trades."""
        if not self.trade_history:
            return 0.0
        return sum(trade.net_pnl for trade in self.trade_history) / len(self.trade_history)


def _utc_session(timestamp: int):
    """Accept Unix timestamps in seconds or milliseconds and return UTC date."""
    seconds = timestamp / 1000 if timestamp > 10_000_000_000 else timestamp
    return datetime.fromtimestamp(seconds, tz=timezone.utc).date()


def _utc_iso(timestamp: int) -> str:
    seconds = timestamp / 1000 if timestamp > 10_000_000_000 else timestamp
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def run_backtest(
    candles: list[Candle],
    starting_balance: float = 1000.0,
    strategy: EmaCrossStrategy | None = None,
    funding_rates: dict[int, float] | None = None,
    warmup_candles: list[Candle] | None = None,
    risk_config: RiskConfig | None = None,
) -> BacktestResult:
    """Backtest close-generated signals at the next candle open.

    Uses UTC calendar-day loss limits. When the daily equity loss threshold is
    reached, an open position is closed at that candle's close and new entries
    are blocked until the next UTC day. Journal entries are scoped to this run
    and are not durable storage or an exchange ledger.
    """
    if (
        not isinstance(starting_balance, (int, float))
        or isinstance(starting_balance, bool)
        or not math.isfinite(starting_balance)
        or starting_balance <= 0
    ):
        raise ValueError("starting_balance must be finite and positive")
    if not isinstance(candles, list) or not all(isinstance(candle, Candle) for candle in candles):
        raise ValueError("candles must be a list of valid Candle objects")
    if any(current.timestamp <= previous.timestamp
           for previous, current in zip(candles, candles[1:])):
        raise ValueError("candles must have strictly increasing timestamps")
    warmup = list(warmup_candles or [])
    if not all(isinstance(candle, Candle) for candle in warmup):
        raise ValueError("warmup_candles must contain only valid Candle objects")
    combined = warmup + candles
    if funding_rates is not None:
        if not isinstance(funding_rates, dict):
            raise ValueError("funding_rates must be a timestamp-to-rate dictionary")
        for timestamp, rate in funding_rates.items():
            if (
                not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0
                or not isinstance(rate, (int, float)) or isinstance(rate, bool)
                or not math.isfinite(rate)
            ):
                raise ValueError("funding rates must use non-negative integer timestamps and finite numeric rates")
    if any(current.timestamp <= previous.timestamp
           for previous, current in zip(combined, combined[1:])):
        raise ValueError("warmup and test candles must be strictly increasing and non-overlapping")

    strategy = strategy or EmaCrossStrategy()
    broker = PaperBroker(starting_balance, config=risk_config)
    # Warm-up candles provide past context only: no simulated fills, risk events,
    # funding, or P&L are generated before the evaluation window begins.
    history: list[Candle] = warmup
    # A signal from the final warm-up close is eligible at the first holdout open.
    # This preserves next-open execution without dropping a boundary trade.
    pending_signal: Side | None = strategy.signal(history) if history else None
    peak = float(starting_balance)
    max_dd = 0.0
    session = None
    session_start_equity = float(starting_balance)
    daily_halted = False
    position_entry_timestamp: int | None = None
    trade_journal: list[TradeJournalEntry] = []
    risk_journal: list[RiskJournalEntry] = []

    def record_trade(trade: Trade | None, exit_timestamp: int) -> None:
        nonlocal position_entry_timestamp
        if trade is None:
            return
        entry_timestamp = position_entry_timestamp
        trade_journal.append(TradeJournalEntry(
            entry_time_utc=_utc_iso(entry_timestamp if entry_timestamp is not None else exit_timestamp),
            exit_time_utc=_utc_iso(exit_timestamp),
            side=trade.side.value,
            entry_price=trade.entry_price,
            exit_price=trade.exit_price,
            quantity=trade.quantity,
            gross_pnl=trade.pnl + trade.fees + trade.funding_cost,
            fees=trade.fees,
            funding_cost=trade.funding_cost,
            net_pnl=trade.pnl,
            exit_reason=trade.reason,
            balance_after=broker.balance,
        ))
        position_entry_timestamp = None

    previous_timestamp_ms: int | None = None
    for candle in candles:
        # Candle timestamps denote interval starts. A funding event between candle
        # starts is applied at the next candle open as a conservative, explicit proxy
        # for the mark price; OHLC data cannot recover the exact settlement mark.
        timestamp_ms = candle.timestamp if candle.timestamp > 10_000_000_000 else candle.timestamp * 1000
        seconds = timestamp_ms / 1000
        if broker.position is not None:
            if funding_rates is None:
                if seconds % (8 * 60 * 60) == 0:
                    broker.apply_funding(candle.open)
            else:
                if previous_timestamp_ms is not None:
                    event_times = sorted(
                        event_ms for event_ms in funding_rates
                        if previous_timestamp_ms < event_ms <= timestamp_ms
                    )
                    for event_ms in event_times:
                        broker.apply_funding(candle.open, funding_rates[event_ms])

                    # Fail closed if an expected standard 8-hour settlement is inside
                    # this interval but absent from the supplied historical series.
                    first_boundary = (previous_timestamp_ms // 28_800_000 + 1) * 28_800_000
                    for boundary_ms in range(first_boundary, timestamp_ms + 1, 28_800_000):
                        if boundary_ms not in funding_rates:
                            raise ValueError(
                                f"Missing historical funding rate at timestamp {boundary_ms} ms"
                            )
        previous_timestamp_ms = timestamp_ms

        candle_session = _utc_session(candle.timestamp)
        if candle_session != session:
            session = candle_session
            session_start_equity = broker.equity(candle.open)
            daily_halted = False

        if pending_signal is not None and not daily_halted:
            if broker.position is not None and pending_signal != broker.position.side:
                record_trade(broker.close_position(candle.open, "signal_reversal"), candle.timestamp)
            if broker.position is None:
                opened = broker.open_position(pending_signal, candle.open)
                if opened is not None:
                    position_entry_timestamp = candle.timestamp
        pending_signal = None

        record_trade(broker.on_candle(candle), candle.timestamp)
        history.append(candle)

        # Measure drawdown conservatively using intrabar adverse/favorable marks,
        # not only candle closes. OHLC cannot reveal the exact path, so when the
        # position survives the candle we use its worst and best observed extremes.
        equity = broker.equity(candle.close)
        adverse_equity = equity
        favorable_equity = equity
        if broker.position is not None:
            if broker.position.side == Side.LONG:
                adverse_equity = broker.equity(candle.low)
                favorable_equity = broker.equity(candle.high)
            else:
                adverse_equity = broker.equity(candle.high)
                favorable_equity = broker.equity(candle.low)
        if not daily_halted and broker.risk.daily_loss_limit_hit(session_start_equity, equity):
            marked_equity = equity
            if broker.position is not None:
                record_trade(broker.close_position(candle.close, "daily_loss_limit"), candle.timestamp)
                equity = broker.balance
            risk_journal.append(RiskJournalEntry(
                time_utc=_utc_iso(candle.timestamp),
                event="daily_loss_limit",
                marked_equity=marked_equity,
                balance_after=broker.balance,
                message="Daily loss threshold reached; new entries blocked until the next UTC day.",
            ))
            daily_halted = True

        if not daily_halted:
            pending_signal = strategy.signal(history)

        peak = max(peak, favorable_equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - min(equity, adverse_equity)) / peak)

    if broker.position is not None and candles:
        record_trade(broker.close_position(candles[-1].close, "end_of_data"), candles[-1].timestamp)

    ending = broker.balance
    peak = max(peak, ending)
    if peak > 0:
        max_dd = max(max_dd, (peak - ending) / peak)
    wins = sum(trade.pnl > 0 for trade in broker.trades)
    losses = sum(trade.pnl <= 0 for trade in broker.trades)
    return BacktestResult(
        starting_balance=starting_balance,
        ending_balance=ending,
        total_return_pct=(ending / starting_balance - 1) * 100,
        trades=len(broker.trades),
        wins=wins,
        losses=losses,
        max_drawdown_pct=max_dd * 100,
        trade_history=tuple(trade_journal),
        risk_events=tuple(risk_journal),
    )
