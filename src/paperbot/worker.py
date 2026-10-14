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


def _session_id(symbol: str, interval: str) -> str:
    return f"{symbol.upper()}:{interval}"


def run_cycle(store: SQLitePaperStore, *, symbol: str, interval: str,
              starting_balance: float, strategy_name: str, history_limit: int = 500) -> str:
    """Run one safe cycle and atomically persist it; data access can be mocked in tests."""
    loaded = store.load(_session_id(symbol, interval))
    diagnostics: list[str] = []
    LOG.info("Fetching public futures candles: symbol=%s interval=%s limit=%d",
             symbol, interval, history_limit)
    candles, market_source = load_public_klines(
        symbol=symbol, interval=interval, limit=history_limit,
        cache_dir=os.getenv("PAPERBOT_CACHE_DIR", ".cache/worker"),
        diagnostics=diagnostics,
    )
    for detail in diagnostics:
        LOG.warning("Market-data provider failed; trying next provider: %s", detail)
    LOG.info("Market data received: provider=%s candles=%d latest_candle_ms=%d",
             market_source, len(candles), candles[-1].timestamp)
    # This worker is specifically a Binance USDⓈ-M Futures simulator. Never
    # advance its account using another exchange's candles: cross-exchange
    # prices can differ and would make the paper results misleading.
    if not market_source.startswith("Binance USDⓈ-M Futures"):
        raise MarketDataError(
            "Binance-only paper mode: refusing non-Binance market data "
            f"from {market_source}. No candles were processed or account state updated; spot-proxy and other exchange fallbacks are disabled."
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
    processed = session.process_new_candles(candles, market_source)
    new_revision = store.save(
        _session_id(symbol, interval), session.to_dict(), expected_revision=revision
    )
    if session.paused_reason:
        return f"PAUSED at revision {new_revision}: {session.paused_reason}"
    if processed == 0:
        return f"No new candles; account revision {new_revision} remains current."
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
        starting_balance = float(os.getenv("PAPERBOT_STARTING_BALANCE", "1000"))
        if not 100 <= starting_balance <= 10000:
            raise ValueError
    except ValueError as exc:
        raise SystemExit("PAPERBOT_STARTING_BALANCE must be between 100 and 10000 USDT") from exc
    strategy_name = os.getenv("PAPERBOT_STRATEGY", "ema")
    if strategy_name not in {"ema", "break-retest"}:
        raise SystemExit("PAPERBOT_STRATEGY must be ema or break-retest")
    poll_seconds = _env_int("PAPERBOT_POLL_SECONDS", 60, 15, 3600)
    database_path = os.getenv("PAPERBOT_DB_PATH", "/var/data/paperbot.sqlite")
    store = SQLitePaperStore(database_path)
    LOG.info("Starting paper-only worker: %s %s strategy=%s poll=%ss",
             symbol, interval, strategy_name, poll_seconds)
    LOG.warning("No live-order adapter is configured; this process only simulates trades.")
    while True:
        if os.getenv("PAPERBOT_PAUSED", "false").strip().lower() in {"1", "true", "yes", "on"}:
            LOG.warning("Kill switch enabled; skipping market processing.")
        else:
            try:
                LOG.info("Beginning paper-trading cycle.")
                LOG.info("%s", run_cycle(
                    store, symbol=symbol, interval=interval,
                    starting_balance=starting_balance, strategy_name=strategy_name,
                ))
            except SessionConflict as exc:
                LOG.error("Concurrent worker conflict; no overwrite occurred: %s", exc)
            except (MarketDataError, OSError, ValueError) as exc:
                LOG.error("Cycle failed safely; account was not updated: %s", exc)
            except Exception:
                LOG.exception("Unexpected cycle failure; investigate before trusting this paper session.")
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
