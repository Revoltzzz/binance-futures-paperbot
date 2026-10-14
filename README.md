# Binance Futures PaperBot

A safety-first Python starter for **local paper trading and backtesting**. It never places real orders. You can run it offline with deterministic synthetic candles or fetch public Binance USDⓈ-M Futures candles without an API key.

> Educational software, not financial advice. Backtests do not predict future results. Futures and leverage can cause rapid losses.

## Features
- EMA crossover baseline (fast EMA 5 / slow EMA 12)
- Local simulated long/short broker with stop-loss and take-profit
- Conservative candle rule: if stop and target both cross in one candle, stop-loss wins
- Risk-based position sizing, leverage cap, fee approximation and daily-loss-limit helper
- CSV OHLC backtesting and deterministic synthetic demo candles
- Public Binance USDⓈ-M Futures kline downloader with retries, validation, incomplete-candle filtering and local cache
- Offline tests with mocked HTTP responses; GitHub Actions CI
- No credentials, signed requests, or live-order code

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

## Fetch real historical candles and paper-backtest them

The public market-data endpoint does not require an API key. Network access is required for this mode.

```bash
paperbot --binance --symbol BTCUSDT --interval 5m --limit 500
paperbot --binance --symbol ETHUSDT --interval 15m --limit 500 --balance 1000
paperbot --binance --symbol BTCUSDT --interval 1h --limit 300
```

Supported symbols are validated as alphanumeric strings; supported intervals include 5m, 15m and 1h. Requests are capped at 1500 rows. Completed candles are sorted and validated; duplicate timestamps, missing intervals, malformed OHLC data and failed requests stop the run with an explicit error. Downloaded completed candles are cached under `.cache/binance/`. A failed network request does not silently backtest stale cache data.

The default `paperbot` command still uses synthetic demo candles and works offline.

## Backtest your own candles
CSV columns required: `timestamp,open,high,low,close`; `volume` is optional.
```bash
paperbot --csv data/BTCUSDT_1h.csv --balance 1000
```

## Safety and limitations
- This program is a historical backtest, not a persistent live paper-trading loop.
- Orders are simulated in memory; restarting resets the account.
- Daily-loss-limit helper is available, but the current backtest runner does not implement daily session resets.
- Funding payments, liquidation engine, exchange precision/minimum-notional rules, slippage and real-time feeds are not implemented.
- Fees are approximated using a configurable flat rate; results are not exchange-grade accounting.
- No real orders, API credentials, or live trading. Any future execution adapter must be separate, opt-in, testnet-first and independently tested.
- Never interpret a positive backtest as evidence of future profitability.

## Project layout
`src/paperbot/` contains market-data, models, strategy, risk, paper broker, CLI and backtest code; `tests/` contains deterministic tests; `.github/` contains CI.
