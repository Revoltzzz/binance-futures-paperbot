from dataclasses import dataclass
from datetime import datetime, timezone

from .models import Candle, Side, Trade
from .paper import PaperBroker
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
    def gross_profit(self) -> float:
        """Sum of positive net trade P&L after modeled costs."""
        return sum(max(0.0, trade.net_pnl) for trade in self.trade_history)

    @property
    def gross_loss(self) -> float:
        """Absolute sum of negative net trade P&L after modeled costs."""
        return sum(abs(min(0.0, trade.net_pnl)) for trade in self.trade_history)

    @property
    def profit_factor(self) -> float | None:
        """Positive P&L divided by absolute negative P&L; None if no losses occurred."""
        loss = self.gross_loss
        return self.gross_profit / loss if loss > 0 else None

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
) -> BacktestResult:
    """Backtest close-generated signals at the next candle open.

    Uses UTC calendar-day loss limits. When the daily equity loss threshold is
    reached, an open position is closed at that candle's close and new entries
    are blocked until the next UTC day. Journal entries are scoped to this run
    and are not durable storage or an exchange ledger.
    """
    if starting_balance <= 0:
        raise ValueError("starting_balance must be positive")
    if any(current.timestamp <= previous.timestamp
           for previous, current in zip(candles, candles[1:])):
        raise ValueError("candles must have strictly increasing timestamps")
    warmup = list(warmup_candles or [])
    combined = warmup + candles
    if any(current.timestamp <= previous.timestamp
           for previous, current in zip(combined, combined[1:])):
        raise ValueError("warmup and test candles must be strictly increasing and non-overlapping")

    strategy = strategy or EmaCrossStrategy()
    broker = PaperBroker(starting_balance)
    # Warm-up candles provide past context only: no simulated fills, risk events,
    # funding, or P&L are generated before the evaluation window begins.
    history: list[Candle] = warmup
    pending_signal: Side | None = None
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

    for candle in candles:
        # The default model assumes USDⓈ-M funding at 00:00, 08:00, and 16:00 UTC.
        # Historical mode instead applies every supplied event that aligns with a candle,
        # since other venues may use different funding schedules.
        seconds = candle.timestamp / 1000 if candle.timestamp > 10_000_000_000 else candle.timestamp
        funding_timestamp_ms = candle.timestamp if candle.timestamp > 10_000_000_000 else candle.timestamp * 1000
        at_default_funding_boundary = seconds % (8 * 60 * 60) == 0
        if broker.position is not None:
            if funding_rates is None:
                if at_default_funding_boundary:
                    broker.apply_funding(candle.open)
            elif funding_timestamp_ms in funding_rates:
                broker.apply_funding(candle.open, funding_rates[funding_timestamp_ms])
            elif at_default_funding_boundary:
                # Preserve an explicit failure for missing expected Binance-style
                # 8-hour events rather than silently substituting the fixed-rate model.
                raise ValueError(
                    f"Missing historical funding rate at timestamp {funding_timestamp_ms} ms"
                )

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

        equity = broker.equity(candle.close)
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

        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)

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
