"""Incremental, session-scoped paper trading over newly completed candles.

This module is deliberately local-only: it uses public candle data and PaperBroker,
never exchange credentials or order endpoints. State lives in the current Streamlit
browser session and is not durable across server restarts.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .binance_data import INTERVAL_MS
from .models import Candle, Side
from .paper import PaperBroker
from .strategy import BreakRetestStrategy, EmaCrossStrategy


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
        if strategy_name not in {"ema", "break-retest"}:
            raise ValueError("strategy_name must be 'ema' or 'break-retest'")
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
        self.strategy = (
            EmaCrossStrategy(fast_period=fast_period, slow_period=slow_period)
            if strategy_name == "ema"
            else BreakRetestStrategy()
        )
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
