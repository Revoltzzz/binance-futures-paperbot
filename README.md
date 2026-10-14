# Binance Futures PaperBot

A safety-first Python starter for **local paper trading and backtesting**. It requires no Binance API keys, makes no exchange requests, and cannot place real orders. The default input is deterministic synthetic OHLC candles so it runs offline.

> Educational software, not financial advice. Backtests do not predict future results. Futures and leverage can cause rapid losses.

## Features
- EMA crossover baseline (fast EMA 5 / slow EMA 12)
- Local simulated long/short broker with stop-loss and take-profit
- Conservative candle rule: if stop and target both cross in one candle, stop-loss wins
- Risk-based position sizing, leverage cap, fee approximation and daily-loss-limit helper
- CSV OHLC backtesting, deterministic demo candles, return and drawdown summary
- Automated tests and GitHub Actions CI
- No exchange credentials or network calls

## Quick start
Python 3.10+ recommended.

```bash
python -m venv .venv
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -e '.[test]'
pytest -q
paperbot
```

Run a longer synthetic demo:
```bash
paperbot --candles 1000 --balance 1000
```

## Backtest your own candles
CSV columns required: `timestamp,open,high,low,close`; `volume` is optional.
```bash
paperbot --csv data/BTCUSDT_1h.csv --balance 1000
```

Only use data you have the right to use. The first version intentionally has no automatic market-data downloader: offline execution must be dependable without credentials or network access.

## Safety and limitations
- Orders are simulated in memory; restarting resets the account.
- Daily-loss-limit helper is available, but the current backtest runner does not implement daily session resets.
- Funding payments, liquidation engine, exchange precision/minimum-notional rules, slippage and real-time feeds are not implemented.
- Fees are approximated using a configurable flat rate; results are not exchange-grade accounting.
- Do not add real API keys or enable live trading as a shortcut. Any future live adapter must be separate, opt-in, testnet-first and independently tested.

## Project layout
`src/paperbot/` contains models, strategy, risk, paper broker, data and backtest; `tests/` contains deterministic tests; `.github/` contains CI workflow.
