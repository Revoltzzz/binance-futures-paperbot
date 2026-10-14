# Binance Futures PaperBot

A safety-first Python starter for **local paper trading and backtesting**. It never places real orders. Run offline with synthetic candles, fetch public Binance USDⓈ-M Futures candles without an API key, or aggregate recent crypto headlines from several public RSS/Atom feeds.

> Educational software, not financial advice. Backtests do not predict future results. Futures and leverage can cause rapid losses.

## Features
- EMA crossover baseline (fast EMA 5 / slow EMA 12)
- Local simulated long/short broker with stop-loss and take-profit
- Risk-based position sizing, leverage cap, fee approximation and daily-loss-limit helper
- Public Binance Futures candle downloader with retries, validation, incomplete-candle filtering and local cache
- Multi-source news aggregation (CoinDesk, Cointelegraph, CryptoSlate and Bitcoin Magazine RSS/Atom feeds)
- Deduplication, publication-time sorting, local news snapshot, one-shot news and recurring refresh mode
- Offline tests with mocked HTTP/RSS responses; GitHub Actions CI
- No exchange credentials, signed requests, or live-order code

## Quick start
Python 3.10+ recommended.

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -e '.[test]'
pytest -q
paperbot
```

## News: latest headlines and automatic refresh

Fetch the latest headlines once:
```bash
paperbot --news-only --news-limit 20
```

Keep refreshing headlines every five minutes until you press Ctrl+C:
```bash
paperbot --watch-news --refresh-seconds 300 --news-limit 20
```

Show current headlines and then run a historical market-data paper backtest:
```bash
paperbot --show-news --binance --symbol BTCUSDT --interval 5m --limit 500
```

The watcher refreshes the configured public feeds each cycle and writes a snapshot to `.cache/news/latest.json`. The default sources are a practical starting set, **not every news outlet or every market**. Feeds may be delayed, unavailable, incomplete, or contain inaccurate claims. Headlines are displayed as context and are not automatically converted into buy/sell orders. The watcher runs only while the command is running; it is not a hosted background service.

## Fetch real historical candles and paper-backtest them

The public market-data endpoint does not require an API key. Network access is required for this mode.

```bash
paperbot --binance --symbol BTCUSDT --interval 5m --limit 500
paperbot --binance --symbol ETHUSDT --interval 15m --limit 500 --balance 1000
paperbot --binance --symbol BTCUSDT --interval 1h --limit 300
```

Completed candles are sorted and validated; duplicate timestamps, missing intervals, malformed OHLC data and failed requests stop the run with an explicit error. Downloaded completed candles are cached under `.cache/binance/`. A failed network request does not silently backtest stale cache data.

## Backtest your own candles
CSV columns required: `timestamp,open,high,low,close`; `volume` is optional.
```bash
paperbot --csv data/BTCUSDT_1h.csv --balance 1000
```

## Safety and limitations
- The news watcher refreshes public feeds; it does not modify its own source code or autonomously retrain a strategy.
- This program is a historical backtest, not a persistent trading loop.
- Orders are simulated in memory; restarting resets the account.
- Daily-loss-limit helper is available, but the current backtest runner does not implement daily session resets.
- Funding payments, liquidation engine, exchange precision/minimum-notional rules, slippage and real-time feeds are not implemented.
- Fees are approximated using a configurable flat rate; results are not exchange-grade accounting.
- No real orders, API credentials, or live trading. Any future execution adapter must be separate, opt-in, testnet-first and independently tested.
- Never interpret positive backtest results or news headlines as evidence of future profitability.

## Project layout
`src/paperbot/` contains market-data, news, models, strategy, risk, paper broker, CLI and backtest code; `tests/` contains deterministic tests; `.github/` contains CI.
