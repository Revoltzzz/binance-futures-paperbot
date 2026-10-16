"""Incremental, session-scoped paper trading over newly completed candles.

This module is deliberately local-only: it uses public candle data and PaperBroker,
never exchange credentials or order endpoints. State lives in the current Streamlit
browser session and is not durable across server restarts.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math

from .binance_data import INTERVAL_MS
from .models import Candle, Side
from .paper import PaperBroker
from .models import Position, Trade
from .risk import RiskConfig, RiskManager
from .strategy import BreakRetestStrategy, EmaCrossStrategy, RsiMeanReversionStrategy


def _utc_day(timestamp: int):
    seconds = timestamp / 1000 if timestamp > 10_000_000_000 else timestamp
    return datetime.fromtimestamp(seconds, tz=timezone.utc).date()


def _utc_iso(timestamp: int) -> str:
    seconds = timestamp / 1000 if timestamp > 10_000_000_000 else timestamp
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


class PaperTradingSession:
    """Process each new completed candle once, retaining virtual account state."""

    def __init__(
        self,
        *,
        symbol: str,
        interval: str,
        market_source: str,
        starting_balance: float,
        strategy_name: str = "ema",
        fast_period: int = 5,
        slow_period: int = 12,
        initial_candles: list[Candle],
    ):
        if interval not in INTERVAL_MS:
            raise ValueError(f"Unsupported interval: {interval}")
        if not initial_candles:
            raise ValueError("initial_candles must not be empty")
        if strategy_name not in {"ema", "break-retest", "rsi-reversion"}:
            raise ValueError("strategy_name must be ema, break-retest, or rsi-reversion")
        if any(
            current.timestamp <= previous.timestamp
            for previous, current in zip(initial_candles, initial_candles[1:])
        ):
            raise ValueError("initial candles must have strictly increasing timestamps")

        self.symbol = symbol.upper()
        self.interval = interval
        self.market_source = market_source
        self.starting_balance = float(starting_balance)
        self.strategy_name = strategy_name
        if strategy_name == "ema":
            self.strategy = EmaCrossStrategy(fast_period=fast_period, slow_period=slow_period)
        elif strategy_name == "break-retest":
            self.strategy = BreakRetestStrategy()
        else:
            self.strategy = RsiMeanReversionStrategy()
        self.broker = PaperBroker(self.starting_balance)
        self.history = list(initial_candles[-500:])
        self.last_timestamp = initial_candles[-1].timestamp
        self.pending_signal: Side | None = None
        self.session_day = _utc_day(self.last_timestamp)
        self.session_start_equity = self.starting_balance
        self.daily_halted = False
        self.entry_timestamp: int | None = None
        self.trades: list[dict] = []
        self.events: list[dict] = []
        self.paused_reason: str | None = None

    def to_dict(self) -> dict:
        """Return a JSON-safe snapshot sufficient to resume without duplicate candles."""
        position = self.broker.position
        config = self.broker.risk.config
        return {
            "schema_version": 1,
            "symbol": self.symbol,
            "interval": self.interval,
            "market_source": self.market_source,
            "starting_balance": self.starting_balance,
            "strategy_name": self.strategy_name,
            "fast_period": getattr(self.strategy, "fast_period", 5),
            "slow_period": getattr(self.strategy, "slow_period", 12),
            "last_timestamp": self.last_timestamp,
            "history": [
                [c.timestamp, c.open, c.high, c.low, c.close, c.volume]
                for c in self.history
            ],
            "pending_signal": self.pending_signal.value if self.pending_signal else None,
            "session_day": self.session_day.isoformat(),
            "session_start_equity": self.session_start_equity,
            "daily_halted": self.daily_halted,
            "entry_timestamp": self.entry_timestamp,
            "paused_reason": self.paused_reason,
            "risk_config": {
                "risk_per_trade": config.risk_per_trade,
                "stop_loss_pct": config.stop_loss_pct,
                "reward_risk": config.reward_risk,
                "max_leverage": config.max_leverage,
                "fee_rate": config.fee_rate,
                "max_daily_loss_pct": config.max_daily_loss_pct,
                "slippage_rate": config.slippage_rate,
                "funding_rate_per_8h": config.funding_rate_per_8h,
            },
            "broker": {
                "balance": self.broker.balance,
                "position": None if position is None else {
                    "side": position.side.value,
                    "quantity": position.quantity,
                    "entry_price": position.entry_price,
                    "stop_price": position.stop_price,
                    "take_profit_price": position.take_profit_price,
                    "entry_fee": position.entry_fee,
                    "funding_cost": position.funding_cost,
                },
                "trades": [
                    {
                        "side": trade.side.value,
                        "entry_price": trade.entry_price,
                        "exit_price": trade.exit_price,
                        "quantity": trade.quantity,
                        "pnl": trade.pnl,
                        "fees": trade.fees,
                        "reason": trade.reason,
                        "funding_cost": trade.funding_cost,
                    }
                    for trade in self.broker.trades
                ],
            },
            "trades": self.trades,
            "events": self.events,
        }

    @classmethod
    def from_dict(cls, state: dict) -> "PaperTradingSession":
        """Restore a validated session snapshot written by to_dict."""
        if not isinstance(state, dict) or state.get("schema_version") != 1:
            raise ValueError("Unsupported or malformed paper-session snapshot")
        raw_history = state.get("history")
        if not isinstance(raw_history, list) or not raw_history:
            raise ValueError("Paper-session snapshot has no candle history")
        history = [Candle(*row) for row in raw_history]
        interval = state.get("interval")
        if interval not in INTERVAL_MS:
            raise ValueError("Snapshot has an unsupported candle interval")
        duration = INTERVAL_MS[interval]
        for candle in history:
            if candle.timestamp % duration != 0:
                raise ValueError("Snapshot candle timestamp is not aligned to its interval")
        for previous, current in zip(history, history[1:]):
            if current.timestamp - previous.timestamp != duration:
                raise ValueError("Snapshot candle history is gapped, duplicated, or out of order")
        raw_starting_balance = state.get("starting_balance")
        if isinstance(raw_starting_balance, bool):
            raise ValueError("Snapshot starting balance must be a finite positive number")
        try:
            starting_balance = float(raw_starting_balance)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Snapshot starting balance must be a finite positive number") from exc
        if not math.isfinite(starting_balance) or starting_balance <= 0:
            raise ValueError("Snapshot starting balance must be a finite positive number")
        session = cls(
            symbol=state["symbol"],
            interval=state["interval"],
            market_source=state["market_source"],
            starting_balance=starting_balance,
            strategy_name=state["strategy_name"],
            fast_period=int(state.get("fast_period", 5)),
            slow_period=int(state.get("slow_period", 12)),
            initial_candles=history,
        )
        if history[-1].timestamp != int(state["last_timestamp"]):
            raise ValueError("Snapshot last timestamp does not match its candle history")
        risk_config = RiskConfig(**state.get("risk_config", {}))
        session.broker.risk = RiskManager(risk_config)
        broker_state = state.get("broker")
        if not isinstance(broker_state, dict):
            raise ValueError("Snapshot has no broker state")
        raw_balance = broker_state.get("balance")
        if isinstance(raw_balance, bool):
            raise ValueError("Snapshot broker balance must be finite and positive")
        try:
            restored_balance = float(raw_balance)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Snapshot broker balance is invalid") from exc
        if not math.isfinite(restored_balance) or restored_balance <= 0:
            raise ValueError("Snapshot broker balance must be finite and positive")
        session.broker.balance = restored_balance
        raw_position = broker_state.get("position")
        if raw_position is None:
            session.broker.position = None
        else:
            if not isinstance(raw_position, dict):
                raise ValueError("Snapshot position must be an object")
            if any(
                isinstance(raw_position.get(key, 0.0), bool)
                for key in (
                    "quantity", "entry_price", "stop_price", "take_profit_price",
                    "entry_fee", "funding_cost",
                )
            ):
                raise ValueError("Snapshot position values must not be booleans")
            try:
                position_values = {
                    "quantity": float(raw_position["quantity"]),
                    "entry_price": float(raw_position["entry_price"]),
                    "stop_price": float(raw_position["stop_price"]),
                    "take_profit_price": float(raw_position["take_profit_price"]),
                    "entry_fee": float(raw_position.get("entry_fee", 0.0)),
                    "funding_cost": float(raw_position.get("funding_cost", 0.0)),
                }
                side = Side(raw_position["side"])
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ValueError("Snapshot position is malformed") from exc
            if not all(math.isfinite(value) for value in position_values.values()):
                raise ValueError("Snapshot position values must be finite")
            if any(position_values[key] <= 0 for key in
                   ("quantity", "entry_price", "stop_price", "take_profit_price")):
                raise ValueError("Snapshot position quantities and prices must be positive")
            if position_values["entry_fee"] < 0:
                raise ValueError("Snapshot position entry fee cannot be negative")
            if side == Side.LONG and not (
                position_values["stop_price"] < position_values["entry_price"] <
                position_values["take_profit_price"]
            ):
                raise ValueError("Snapshot long position stop/target levels are inconsistent")
            if side == Side.SHORT and not (
                position_values["take_profit_price"] < position_values["entry_price"] <
                position_values["stop_price"]
            ):
                raise ValueError("Snapshot short position stop/target levels are inconsistent")
            session.broker.position = Position(side=side, **position_values)
        def finite_number(value, label: str, *, positive: bool = False, nonnegative: bool = False) -> float:
            if isinstance(value, bool):
                raise ValueError(f"Snapshot {label} must be a finite number")
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"Snapshot {label} must be a finite number") from exc
            if not math.isfinite(number):
                raise ValueError(f"Snapshot {label} must be a finite number")
            if positive and number <= 0:
                raise ValueError(f"Snapshot {label} must be positive")
            if nonnegative and number < 0:
                raise ValueError(f"Snapshot {label} cannot be negative")
            return number

        raw_broker_trades = broker_state.get("trades", [])
        if not isinstance(raw_broker_trades, list):
            raise ValueError("Snapshot broker trade history must be a list")
        restored_trades: list[Trade] = []
        for index, item in enumerate(raw_broker_trades):
            label = f"broker trade {index}"
            if not isinstance(item, dict):
                raise ValueError(f"Snapshot {label} must be an object")
            try:
                side = Side(item["side"])
                reason = item["reason"]
                if not isinstance(reason, str) or not reason.strip():
                    raise ValueError(f"Snapshot {label} reason must be a non-empty string")
                restored_trades.append(Trade(
                    side=side,
                    entry_price=finite_number(item["entry_price"], f"{label} entry price", positive=True),
                    exit_price=finite_number(item["exit_price"], f"{label} exit price", positive=True),
                    quantity=finite_number(item["quantity"], f"{label} quantity", positive=True),
                    pnl=finite_number(item["pnl"], f"{label} P&L"),
                    fees=finite_number(item["fees"], f"{label} fees", nonnegative=True),
                    reason=reason,
                    funding_cost=finite_number(item.get("funding_cost", 0.0), f"{label} funding cost"),
                ))
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"Snapshot {label} is malformed: {exc}") from exc
        session.broker.trades = restored_trades
        raw_signal = state.get("pending_signal")
        session.pending_signal = Side(raw_signal) if raw_signal else None
        session.session_day = datetime.fromisoformat(state["session_day"]).date()
        raw_session_start_equity = state.get("session_start_equity")
        if isinstance(raw_session_start_equity, bool):
            raise ValueError("Snapshot session-start equity must be a finite positive number")
        try:
            session_start_equity = float(raw_session_start_equity)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Snapshot session-start equity is invalid") from exc
        # A NaN or non-positive baseline can silently disable the daily-loss guard
        # after a restart because comparisons against NaN are always false.
        if not math.isfinite(session_start_equity) or session_start_equity <= 0:
            raise ValueError("Snapshot session-start equity must be finite and positive")
        session.session_start_equity = session_start_equity
        if not isinstance(state.get("daily_halted"), bool):
            raise ValueError("Snapshot daily_halted flag must be boolean")
        session.daily_halted = state["daily_halted"]
        raw_entry_timestamp = state.get("entry_timestamp")
        if raw_entry_timestamp is not None and (
            isinstance(raw_entry_timestamp, bool)
            or not isinstance(raw_entry_timestamp, int)
            or raw_entry_timestamp <= 0
        ):
            raise ValueError("Snapshot entry timestamp must be a positive integer or null")
        session.entry_timestamp = raw_entry_timestamp
        session.paused_reason = state.get("paused_reason")
        raw_journal = state.get("trades", [])
        if not isinstance(raw_journal, list):
            raise ValueError("Snapshot trade journal must be a list")
        journal: list[dict] = []
        for index, item in enumerate(raw_journal):
            label = f"trade journal entry {index}"
            if not isinstance(item, dict):
                raise ValueError(f"Snapshot {label} must be an object")
            required = (
                "Entry time (UTC)", "Exit time (UTC)", "Side", "Entry price",
                "Exit price", "Quantity", "Net P&L (USDT)", "Fees (USDT)",
                "Funding cost (USDT)", "Exit reason", "Balance after (USDT)",
            )
            if any(key not in item for key in required):
                raise ValueError(f"Snapshot {label} is missing required fields")
            if item["Side"] not in {"LONG", "SHORT"}:
                raise ValueError(f"Snapshot {label} has an invalid side")
            if not isinstance(item["Exit reason"], str) or not item["Exit reason"].strip():
                raise ValueError(f"Snapshot {label} has an invalid exit reason")
            try:
                entry_time = datetime.fromisoformat(item["Entry time (UTC)"].replace("Z", "+00:00"))
                exit_time = datetime.fromisoformat(item["Exit time (UTC)"].replace("Z", "+00:00"))
            except (AttributeError, TypeError, ValueError) as exc:
                raise ValueError(f"Snapshot {label} has an invalid timestamp") from exc
            if entry_time.tzinfo is None or exit_time.tzinfo is None or exit_time < entry_time:
                raise ValueError(f"Snapshot {label} timestamps are inconsistent")
            finite_number(item["Entry price"], f"{label} entry price", positive=True)
            finite_number(item["Exit price"], f"{label} exit price", positive=True)
            finite_number(item["Quantity"], f"{label} quantity", positive=True)
            finite_number(item["Net P&L (USDT)"], f"{label} P&L")
            finite_number(item["Fees (USDT)"], f"{label} fees", nonnegative=True)
            finite_number(item["Funding cost (USDT)"], f"{label} funding cost")
            finite_number(item["Balance after (USDT)"], f"{label} balance", positive=True)
            journal.append(dict(item))
        raw_events = state.get("events", [])
        if not isinstance(raw_events, list):
            raise ValueError("Snapshot event journal must be a list")
        events: list[dict] = []
        for index, item in enumerate(raw_events):
            label = f"event journal entry {index}"
            if not isinstance(item, dict):
                raise ValueError(f"Snapshot {label} must be an object")
            if item.get("Event") != "daily_loss_limit":
                raise ValueError(f"Snapshot {label} has an unknown event type")
            try:
                event_time = datetime.fromisoformat(item["Time (UTC)"].replace("Z", "+00:00"))
            except (KeyError, AttributeError, TypeError, ValueError) as exc:
                raise ValueError(f"Snapshot {label} has an invalid timestamp") from exc
            if event_time.tzinfo is None:
                raise ValueError(f"Snapshot {label} timestamp must include a timezone")
            finite_number(item.get("Marked equity (USDT)"), f"{label} marked equity", positive=True)
            finite_number(item.get("Balance after (USDT)"), f"{label} balance", positive=True)
            if not isinstance(item.get("Details"), str):
                raise ValueError(f"Snapshot {label} details must be a string")
            events.append(dict(item))
        session.trades = journal
        session.events = events
        return session

    def _record_trade(self, trade, exit_timestamp: int) -> None:
        if trade is None:
            return
        self.trades.append({
            "Entry time (UTC)": _utc_iso(
                self.entry_timestamp if self.entry_timestamp is not None else exit_timestamp
            ),
            "Exit time (UTC)": _utc_iso(exit_timestamp),
            "Side": trade.side.value.upper(),
            "Entry price": trade.entry_price,
            "Exit price": trade.exit_price,
            "Quantity": trade.quantity,
            "Net P&L (USDT)": trade.pnl,
            "Fees (USDT)": trade.fees,
            "Funding cost (USDT)": trade.funding_cost,
            "Exit reason": trade.reason,
            "Balance after (USDT)": self.broker.balance,
        })
        self.entry_timestamp = None

    def process_new_candles(self, candles: list[Candle], market_source: str) -> int:
        """Process only unseen completed candles; pause instead of trading across gaps."""
        if self.paused_reason:
            return 0
        if market_source != self.market_source:
            self.paused_reason = (
                f"Market-data provider changed from {self.market_source} to {market_source}. "
                "Reset the session before continuing so prices from different providers are not mixed."
            )
            return 0
        if not candles:
            return 0

        new_candles = [candle for candle in candles if candle.timestamp > self.last_timestamp]
        if not new_candles:
            return 0

        interval_ms = INTERVAL_MS[self.interval]
        if new_candles[0].timestamp - self.last_timestamp != interval_ms:
            self.paused_reason = (
                "A gap was detected between the last processed candle and current market data. "
                "Reset the session to avoid trading on incomplete history."
            )
            return 0

        processed = 0
        for candle in new_candles:
            if candle.timestamp - self.last_timestamp != interval_ms:
                self.paused_reason = (
                    "A candle gap was detected. The session has paused; reset it before continuing."
                )
                break

            timestamp_ms = (
                candle.timestamp if candle.timestamp > 10_000_000_000
                else candle.timestamp * 1000
            )
            if self.broker.position is not None and timestamp_ms % 28_800_000 == 0:
                self.broker.apply_funding(candle.open)

            candle_day = _utc_day(candle.timestamp)
            if candle_day != self.session_day:
                self.session_day = candle_day
                self.session_start_equity = self.broker.equity(candle.open)
                self.daily_halted = False

            if self.pending_signal is not None and not self.daily_halted:
                if self.broker.position is not None and self.pending_signal != self.broker.position.side:
                    self._record_trade(
                        self.broker.close_position(candle.open, "signal_reversal"),
                        candle.timestamp,
                    )
                if self.broker.position is None:
                    opened = self.broker.open_position(self.pending_signal, candle.open)
                    if opened is not None:
                        self.entry_timestamp = candle.timestamp
            self.pending_signal = None

            self._record_trade(self.broker.on_candle(candle), candle.timestamp)
            self.history.append(candle)
            if len(self.history) > 500:
                self.history = self.history[-500:]

            equity = self.broker.equity(candle.close)
            if not self.daily_halted and self.broker.risk.daily_loss_limit_hit(
                self.session_start_equity, equity
            ):
                marked_equity = equity
                if self.broker.position is not None:
                    self._record_trade(
                        self.broker.close_position(candle.close, "daily_loss_limit"),
                        candle.timestamp,
                    )
                    equity = self.broker.balance
                self.events.append({
                    "Time (UTC)": _utc_iso(candle.timestamp),
                    "Event": "daily_loss_limit",
                    "Marked equity (USDT)": marked_equity,
                    "Balance after (USDT)": self.broker.balance,
                    "Details": "Daily loss threshold reached; new entries blocked until the next UTC day.",
                })
                self.daily_halted = True

            if not self.daily_halted:
                self.pending_signal = self.strategy.signal(self.history)

            self.last_timestamp = candle.timestamp
            processed += 1

        return processed
