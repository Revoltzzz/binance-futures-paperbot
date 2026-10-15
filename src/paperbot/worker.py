"""Long-running public-data paper worker. No exchange order endpoints exist here."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

from .binance_data import INTERVAL_MS, MarketDataError
from .public_market_data import load_public_klines
from .session import PaperTradingSession
from .review import summarize_trades
from .storage import SQLitePaperStore, SessionConflict

LOG = logging.getLogger("paperbot.worker")


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _error_backoff_seconds(poll_seconds: int, consecutive_failures: int,
                          maximum_seconds: int = 900) -> int:
    """Bound retry frequency during persistent market-data or state failures."""
    if poll_seconds < 1 or consecutive_failures < 0 or maximum_seconds < 1:
        raise ValueError("backoff inputs must be positive (failure count may be zero)")
    multiplier = 2 ** min(consecutive_failures, 10)
    return min(poll_seconds * multiplier, maximum_seconds)


def _session_id(symbol: str, interval: str) -> str:
    return f"{symbol.upper()}:{interval}"


def _is_binance_eligibility_restriction(error: BaseException) -> bool:
    """Recognize Binance's explicit restricted-location response, not generic 451s."""
    message = str(error).lower()
    return (
        "http error 451" in message
        and "restricted location" in message
        and "binance" in message
    )


def run_cycle(store: SQLitePaperStore, *, symbol: str, interval: str,
              starting_balance: float, strategy_name: str, history_limit: int = 500,
              market_data_mode: str = "binance-only") -> str:
    """Run one safe cycle and atomically persist it; never places exchange orders."""
    if market_data_mode not in {"binance-only", "derivatives-fallback", "okx-only"}:
        raise ValueError("market_data_mode must be binance-only, derivatives-fallback, or okx-only")
    loaded = store.load(_session_id(symbol, interval))
    diagnostics: list[str] = []
    LOG.info("Fetching public futures candles: symbol=%s interval=%s limit=%d mode=%s",
             symbol, interval, history_limit, market_data_mode)
    candles, market_source = load_public_klines(
        symbol=symbol, interval=interval, limit=history_limit,
        cache_dir=os.getenv("PAPERBOT_CACHE_DIR", ".cache/worker"),
        diagnostics=diagnostics,
        binance_only=(market_data_mode == "binance-only"),
        okx_only=(market_data_mode == "okx-only"),
    )
    for detail in diagnostics:
        LOG.warning("Market-data provider failed: %s", detail)
    LOG.info("Market data received: provider=%s candles=%d latest_candle_ms=%d",
             market_source, len(candles), candles[-1].timestamp)
    # Binance-only is the strict default. Derivatives-fallback is an explicit
    # paper-research mode that permits only Bybit/OKX perpetual futures. Spot
    # candles are never accepted, and source identity is persisted per session.
    allowed_sources = ("Binance USDⓈ-M Futures",)
    if market_data_mode == "derivatives-fallback":
        allowed_sources += (
            "Bybit USDT linear perpetual (fallback)",
            "OKX USDT perpetual swap (fallback)",
        )
    elif market_data_mode == "okx-only":
        allowed_sources = ("OKX USDT perpetual swap (fallback)",)
    if not market_source.startswith(allowed_sources):
        mode_label = {"binance-only": "Binance-only", "derivatives-fallback": "derivatives-fallback", "okx-only": "OKX-only"}[market_data_mode]
        raise MarketDataError(
            f"{mode_label} paper mode: refusing unsupported market data "
            f"from {market_source}. spot-proxy data is never accepted; no account state was updated."
        )
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    latest_age_ms = now_ms - (candles[-1].timestamp + INTERVAL_MS[interval])
    if latest_age_ms > INTERVAL_MS[interval] * 3:
        raise MarketDataError("Latest completed candle is stale; refusing to process it.")

    if loaded is None:
        session = PaperTradingSession(
            symbol=symbol, interval=interval, market_source=market_source,
            starting_balance=starting_balance, strategy_name=strategy_name,
            initial_candles=candles,
        )
        store.save(_session_id(symbol, interval), session.to_dict(), expected_revision=None)
        return f"Initialized {symbol} {interval} paper account; waiting for new candles."

    revision, state = loaded
    session = PaperTradingSession.from_dict(state)
    if session.symbol != symbol.upper() or session.interval != interval:
        raise ValueError("Stored paper account does not match configured symbol/interval")
    previous_pause_reason = session.paused_reason
    # Fail closed when recent realized performance is persistently negative.
    # This is a safety circuit breaker, not an optimizer: it never changes
    # parameters or assumes that a different strategy will be profitable.
    if session.paused_reason is None and len(session.trades) >= 10:
        recent_review = summarize_trades(session.trades, window=14)
        if (
            recent_review["sample_size"] >= 10
            and recent_review["expectancy_per_trade"] is not None
            and recent_review["expectancy_per_trade"] <= 0
        ):
            session.paused_reason = (
                "Performance circuit breaker: the most recent "
                f"{recent_review['sample_size']} closed trades have non-positive "
                f"net expectancy ({recent_review['expectancy_per_trade']:.4f} USDT/trade). "
                "Trading is paused for review; select and validate a strategy before resetting."
            )
    processed = session.process_new_candles(candles, market_source)
    # Polling between candle closes is normal. Avoid rewriting an identical
    # SQLite snapshot on every poll, but persist a newly-triggered safety pause.
    if processed == 0 and session.paused_reason == previous_pause_reason:
        if session.paused_reason:
            return f"PAUSED at revision {revision}: {session.paused_reason}"
        return f"No new candles; account revision {revision} remains current."
    new_revision = store.save(
        _session_id(symbol, interval), session.to_dict(), expected_revision=revision
    )
    if session.paused_reason:
        return f"PAUSED at revision {new_revision}: {session.paused_reason}"
    equity = session.broker.equity(candles[-1].close)
    review = summarize_trades(session.trades, window=20)
    if review["sample_size"]:
        review_text = (
            "last%d win_rate=%.1f%% expectancy=%.4f USDT/trade net_pnl=%.2f USDT"
            % (
                review["sample_size"],
                review["win_rate_pct"],
                review["expectancy_per_trade"],
                review["net_pnl"],
            )
        )
    else:
        review_text = "no closed trades yet"
    return (
        f"Processed {processed} candle(s); revision={new_revision}; "
        f"last={session.last_timestamp}; balance={session.broker.balance:.2f} USDT; "
        f"equity={equity:.2f} USDT; closed_trades={len(session.trades)}; "
        f"review={review_text}. Strategy parameters are not auto-changed."
    )


def main() -> None:
    logging.basicConfig(
        level=os.getenv("PAPERBOT_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    symbol = os.getenv("PAPERBOT_SYMBOL", "BTCUSDT").upper()
    interval = os.getenv("PAPERBOT_INTERVAL", "5m")
    if interval not in {"5m", "15m", "1h"}:
        raise SystemExit("PAPERBOT_INTERVAL must be one of: 5m, 15m, 1h")
    if symbol not in {"BTCUSDT", "ETHUSDT"}:
        raise SystemExit("PAPERBOT_SYMBOL must be BTCUSDT or ETHUSDT")
    try:
        starting_balance = float(os.getenv("PAPERBOT_STARTING_BALANCE", "100"))
        if not 100 <= starting_balance <= 10000:
            raise ValueError
    except ValueError as exc:
        raise SystemExit("PAPERBOT_STARTING_BALANCE must be between 100 and 10000 USDT") from exc
    strategy_name = os.getenv("PAPERBOT_STRATEGY", "ema")
    if strategy_name not in {"ema", "break-retest"}:
        raise SystemExit("PAPERBOT_STRATEGY must be ema or break-retest")
    poll_seconds = _env_int("PAPERBOT_POLL_SECONDS", 60, 15, 3600)
    max_error_backoff = _env_int("PAPERBOT_MAX_ERROR_BACKOFF_SECONDS", 900, 60, 3600)
    database_path = os.getenv("PAPERBOT_DB_PATH", "/var/data/paperbot.sqlite")
    store = SQLitePaperStore(database_path)
    market_data_mode = os.getenv("PAPERBOT_MARKET_DATA_MODE", "binance-only").strip().lower()
    if market_data_mode not in {"binance-only", "derivatives-fallback"}:
        raise SystemExit(
            "PAPERBOT_MARKET_DATA_MODE must be binance-only or derivatives-fallback"
        )
    LOG.info("Starting paper-only worker: %s %s strategy=%s data_mode=%s poll=%ss",
             symbol, interval, strategy_name, market_data_mode, poll_seconds)
    if market_data_mode == "derivatives-fallback":
        LOG.warning(
            "Using alternate derivatives data for paper research only; results are not Binance-specific."
        )
    LOG.warning("No live-order adapter is configured; this process only simulates trades.")
    consecutive_failures = 0
    while True:
        sleep_seconds = poll_seconds
        if os.getenv("PAPERBOT_PAUSED", "false").strip().lower() in {"1", "true", "yes", "on"}:
            LOG.warning("Kill switch enabled; skipping market processing.")
        else:
            try:
                LOG.info("Beginning paper-trading cycle.")
                LOG.info("%s", run_cycle(
                    store, symbol=symbol, interval=interval,
                    starting_balance=starting_balance, strategy_name=strategy_name,
                    market_data_mode=market_data_mode,
                ))
                if consecutive_failures:
                    LOG.info("Paper worker recovered after %d consecutive failed cycle(s).",
                             consecutive_failures)
                consecutive_failures = 0
            except SessionConflict as exc:
                consecutive_failures += 1
                LOG.error("Concurrent worker conflict; no overwrite occurred: %s", exc)
                sleep_seconds = _error_backoff_seconds(
                    poll_seconds, consecutive_failures, max_error_backoff
                )
            except (MarketDataError, OSError, ValueError) as exc:
                consecutive_failures += 1
                if market_data_mode == "binance-only" and _is_binance_eligibility_restriction(exc):
                    LOG.critical(
                        "Binance explicitly reports this service as unavailable from a restricted location. "
                        "This is an account/eligibility or jurisdiction issue, not a transient coding error. "
                        "Do not use a proxy or relocate the worker to evade the restriction; contact Binance "
                        "support and confirm eligibility. Paper account remains unchanged. Error: %s",
                        exc,
                    )
                    # Avoid repeatedly hammering an endpoint that has already
                    # returned a permanent eligibility restriction.
                    sleep_seconds = max_error_backoff
                else:
                    LOG.error("Cycle failed safely; account was not updated: %s", exc)
                    sleep_seconds = _error_backoff_seconds(
                        poll_seconds, consecutive_failures, max_error_backoff
                    )
            except Exception:
                consecutive_failures += 1
                LOG.exception("Unexpected cycle failure; investigate before trusting this paper session.")
                sleep_seconds = _error_backoff_seconds(
                    poll_seconds, consecutive_failures, max_error_backoff
                )
        if sleep_seconds > poll_seconds:
            LOG.warning("Backing off for %d seconds after %d consecutive failed cycle(s).",
                        sleep_seconds, consecutive_failures)
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()
