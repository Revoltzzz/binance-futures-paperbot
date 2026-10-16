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
from .models import Candle
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


def _is_paused_result(result: str) -> bool:
    """Identify a persisted safety pause without interpreting its reason."""
    return result.startswith("PAUSED at revision ")


def _validate_worker_candles(candles: object, interval: str) -> list[Candle]:
    """Fail closed on malformed, misaligned, duplicated, or gapped candle history."""
    if interval not in INTERVAL_MS:
        raise MarketDataError(f"Unsupported candle interval: {interval}")
    if not isinstance(candles, list) or not candles:
        raise MarketDataError("Market-data provider returned no candles")
    if not all(isinstance(candle, Candle) for candle in candles):
        raise MarketDataError("Market-data provider returned malformed candles")
    duration = INTERVAL_MS[interval]
    for candle in candles:
        if candle.timestamp % duration != 0:
            raise MarketDataError(
                f"Candle timestamp is not aligned to {interval}: {candle.timestamp}"
            )
    for previous, current in zip(candles, candles[1:]):
        difference = current.timestamp - previous.timestamp
        if difference != duration:
            kind = "missing" if difference > duration else "duplicate or out-of-order"
            raise MarketDataError(
                f"Market-data history contains {kind} candles: "
                f"{previous.timestamp}, {current.timestamp}"
            )
    return candles


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
    if loaded is not None:
        revision, state = loaded
        paused_reason = state.get("paused_reason") if isinstance(state, dict) else None
        legacy_okx_label_pause = (
            "Market-data provider changed from OKX USDT perpetual swap (fallback) "
            "to OKX USDT perpetual swap. Reset the session before continuing so "
            "prices from different providers are not mixed."
        )
        # A persistent safety pause should not trigger a needless network request
        # every polling cycle. The one exact legacy source-label pause is exempt
        # because the migration below must verify that the provider is now the
        # canonical OKX source. Never clear performance or risk pauses here.
        if paused_reason and paused_reason != legacy_okx_label_pause:
            return f"PAUSED at revision {revision}: {paused_reason}"
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
    candles = _validate_worker_candles(candles, interval)
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
        # This mode accepts exactly the configured primary source label, not
        # fallback-labelled data that merely shares the same prefix.
        allowed_sources = ()
    source_allowed = (
        market_source == "OKX USDT perpetual swap"
        if market_data_mode == "okx-only"
        else market_source.startswith(allowed_sources)
    )
    if not source_allowed:
        mode_label = {"binance-only": "Binance-only", "derivatives-fallback": "derivatives-fallback", "okx-only": "OKX-only"}[market_data_mode]
        raise MarketDataError(
            f"{mode_label} paper mode: refusing unsupported market data "
            f"from {market_source}. spot-proxy data is never accepted; no account state was updated."
        )
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    latest_close_ms = candles[-1].timestamp + INTERVAL_MS[interval]
    # Reject future/incomplete bars rather than trusting a provider or local clock
    # enough to advance the account on a candle that has not actually closed.
    if latest_close_ms > now_ms + 1_000:
        raise MarketDataError("Latest candle is not complete; refusing to process it.")
    latest_age_ms = now_ms - latest_close_ms
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
    # PR #32 corrected the configured OKX provider label. Older persisted
    # sessions may carry the former "(fallback)" label; migrate only this exact
    # known alias and only its corresponding label-change pause. Never clear a
    # performance, data-gap, daily-loss, or other unrelated pause.
    legacy_okx_source = "OKX USDT perpetual swap (fallback)"
    canonical_okx_source = "OKX USDT perpetual swap"
    legacy_label_pause = (
        f"Market-data provider changed from {legacy_okx_source} to {canonical_okx_source}. "
        "Reset the session before continuing so prices from different providers are not mixed."
    )
    if (
        market_data_mode == "okx-only"
        and session.market_source == legacy_okx_source
        and market_source == canonical_okx_source
        and session.paused_reason == legacy_label_pause
    ):
        session.market_source = canonical_okx_source
        session.paused_reason = None
        LOG.warning("Migrated legacy OKX source label; underlying provider identity is unchanged.")
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
        profit_factor = (
            "%.2f" % review["profit_factor"]
            if review["profit_factor"] is not None else "n/a"
        )
        review_text = (
            "last%d win_rate=%.1f%% expectancy=%.4f USDT/trade "
            "net_pnl=%.2f USDT profit_factor=%s max_drawdown=%.2f USDT"
            % (
                review["sample_size"],
                review["win_rate_pct"],
                review["expectancy_per_trade"],
                review["net_pnl"],
                profit_factor,
                review["max_drawdown_usdt"],
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
    if market_data_mode not in {"binance-only", "derivatives-fallback", "okx-only"}:
        raise SystemExit(
            "PAPERBOT_MARKET_DATA_MODE must be binance-only, derivatives-fallback, or okx-only"
        )
    LOG.info("Starting paper-only worker: %s %s strategy=%s data_mode=%s poll=%ss",
             symbol, interval, strategy_name, market_data_mode, poll_seconds)
    if market_data_mode in {"derivatives-fallback", "okx-only"}:
        LOG.warning(
            "Using alternate derivatives data for paper research only; results are not Binance-specific."
        )
    if market_data_mode == "okx-only":
        LOG.info("OKX-only mode avoids Binance/Bybit requests; public OKX USDT perpetual swaps are the sole market-data source.")
    LOG.warning("No live-order adapter is configured; this process only simulates trades.")
    if os.getenv("PAPERBOT_CHECK_BYBIT_DEMO_CONNECTIVITY", "false").strip().lower() in {"1", "true", "yes", "on"}:
        try:
            from .bybit_demo import check_public_connectivity
            probe = check_public_connectivity()
            LOG.info(
                "Bybit demo endpoint connectivity: reachable=%s endpoint=%s authenticated=%s",
                probe["reachable"], probe["endpoint"], probe["authenticated"],
            )
        except Exception as exc:
            LOG.warning("Bybit demo endpoint connectivity check failed: %s", exc)
    paused_poll_seconds = _env_int("PAPERBOT_PAUSED_POLL_SECONDS", 300, 60, 3600)
    consecutive_failures = 0
    last_paused_result: str | None = None
    while True:
        sleep_seconds = poll_seconds
        if os.getenv("PAPERBOT_PAUSED", "false").strip().lower() in {"1", "true", "yes", "on"}:
            LOG.warning("Kill switch enabled; skipping market processing.")
        else:
            try:
                result = run_cycle(
                    store, symbol=symbol, interval=interval,
                    starting_balance=starting_balance, strategy_name=strategy_name,
                    market_data_mode=market_data_mode,
                )
                if _is_paused_result(result):
                    # Keep checking the local persisted state so an operator's
                    # deliberate reset can be noticed, but do not spam logs or
                    # poll market data every minute while the safety pause holds.
                    if result != last_paused_result:
                        LOG.warning("%s", result)
                    else:
                        LOG.debug("Safety-paused paper session remains unchanged.")
                    last_paused_result = result
                    sleep_seconds = max(poll_seconds, paused_poll_seconds)
                else:
                    last_paused_result = None
                    LOG.info("Beginning paper-trading cycle.")
                    LOG.info("%s", result)
                if consecutive_failures:
                    LOG.info("Paper worker recovered after %d consecutive failed cycle(s).",
                             consecutive_failures)
                consecutive_failures = 0
            except SessionConflict as exc:
                last_paused_result = None
                consecutive_failures += 1
                LOG.error("Concurrent worker conflict; no overwrite occurred: %s", exc)
                sleep_seconds = _error_backoff_seconds(
                    poll_seconds, consecutive_failures, max_error_backoff
                )
            except (MarketDataError, OSError, ValueError) as exc:
                last_paused_result = None
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
                last_paused_result = None
                consecutive_failures += 1
                LOG.exception("Unexpected cycle failure; investigate before trusting this paper session.")
                sleep_seconds = _error_backoff_seconds(
                    poll_seconds, consecutive_failures, max_error_backoff
                )
        if sleep_seconds > poll_seconds:
            LOG.info("Sleeping for %d seconds (base poll interval %d seconds).",
                     sleep_seconds, poll_seconds)
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()
