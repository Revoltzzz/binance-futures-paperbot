# Binance Futures PaperBot

A safety-first Python starter for **local paper trading and backtesting**. It never places real orders. Run offline with synthetic candles, fetch public Binance USDⓈ-M Futures candles without an API key, or aggregate recent crypto headlines from several public RSS/Atom feeds.

> Educational software, not financial advice. Backtests do not predict future results. Futures and leverage can cause rapid losses.

## Features
- EMA crossover baseline (fast EMA 5 / slow EMA 12)
- Local simulated long/short broker with stop-loss and take-profit
- Risk-based position sizing, leverage cap, fee approximation and daily-loss-limit helper
- Public Binance Futures candle downloader with retries, validation, incomplete-candle filtering and local cache
- Browser dashboard tries Binance USDⓈ-M Futures, Bybit linear perpetuals, and OKX USDT perpetual swaps before an explicitly labelled Kraken spot proxy; provider failures are shown
- Multi-source news aggregation (CoinDesk, Cointelegraph, CryptoSlate and Bitcoin Magazine RSS/Atom feeds)
- Deduplication, publication-time sorting, local news snapshot, one-shot news and recurring refresh mode
- Offline tests with mocked HTTP/RSS responses; GitHub Actions CI
- No exchange credentials, signed requests, or live-order code

## Browser dashboard (no local setup)

The web dashboard is in `app.py` and is ready for Streamlit Community Cloud. It displays public market candles for BTCUSDT or ETHUSDT, an EMA backtest, a Strategy Lab for EMA vs. Break-and-Retest comparison and three-fold walk-forward evaluation, and recent headlines. It also includes an incremental paper-trading session that processes newly completed candles once, simulates entries/exits, and applies the existing stop-loss/take-profit, modeled costs, and daily-loss guard. The virtual account is held in the current Streamlit browser session only; it is **not durable across server restarts** and is not a 24/7 background worker. Provider order: Binance USDⓈ-M Futures, Bybit USDT linear perpetual, OKX USDT perpetual swap, then Kraken spot proxy if all derivatives APIs are blocked. Provider failures are shown in the dashboard. The source is visibly labeled. **Kraken is spot data, not futures data**, so it is only a rough market proxy and cannot validate futures-specific behavior. OKX public swap candles need no API key. Different providers have different candles and prices, so note the provider before interpreting results. It has automatic browser refresh and a manual refresh control. It does **not** place real orders or need API keys.

**Open the hosted-app setup page:** [Deploy PaperBot to Streamlit Community Cloud](https://share.streamlit.io/deploy?repository=Revoltzzz%2Fbinance-futures-paperbot&branch=main&mainModule=app.py)

To publish it, sign in to Streamlit Community Cloud with GitHub, select this repository, branch `main`, and main file `app.py`, then click Deploy. Hosting requires your own Streamlit authorization; repository changes alone cannot publish an app into your hosting account. The first deployment can take a few minutes.

## Persistent background paper worker

The Streamlit dashboard is for viewing and manual analysis. For an always-on paper session, this repository also includes a separate worker process and a SQLite state store. The worker polls completed public candles, saves the virtual account, open position, risk state, pending signal, recent candle history and trade journal to SQLite, and uses revision checks to prevent two worker instances from silently overwriting each other. The worker defaults to Binance-only mode; the hosted Render configuration explicitly enables **derivatives-fallback paper research** because the current deployment receives Binance HTTP 451 (restricted-location) and Bybit HTTP 403 responses. In fallback mode it accepts OKX USDT perpetual swaps (or Bybit USDT linear perpetuals when reachable), labels the source, and does not claim the results represent Binance execution. Kraken spot proxy candles are never accepted by the persistent worker. Software cannot legitimately remove an exchange's regional eligibility restriction; use an exchange/API endpoint you are eligible to access if Binance-specific validation is required. The worker also pauses if candle history has a gap or data is stale.

**Hosted deployment:** the included `render.yaml` defines a Render background worker with a persistent disk mounted at `/var/data`. Connect the repository to Render and create a Blueprint from `render.yaml`. Persistent disks/background workers may require a paid Render plan; check the current plan and pricing before deploying. The worker needs no Binance API keys. Default settings are BTCUSDT, 5m, a 100 USDT virtual balance and EMA crossover. The starting balance is simulated money only.

The worker has a conservative performance circuit breaker: after at least 10 closed trades, if the most recent sample (up to 14 trades) has non-positive net expectancy after modeled costs, it persists a pause instead of continuing to trade. This is a fail-safe, not automatic strategy optimization. Review the trade journal, validate any strategy changes on separate historical holdout data, and deliberately reset the paper session before resuming. No strategy is promoted based on synthetic demo-candle results.

Supported environment settings:
- `PAPERBOT_SYMBOL`: `BTCUSDT` or `ETHUSDT`
- `PAPERBOT_INTERVAL`: `5m`, `15m` or `1h`
- `PAPERBOT_STARTING_BALANCE`: 100–10,000 virtual USDT
- `PAPERBOT_STRATEGY`: `ema` or `break-retest`
- `PAPERBOT_DB_PATH`: defaults to `/var/data/paperbot.sqlite` in the Render blueprint
- `PAPERBOT_POLL_SECONDS`: 15–3600 seconds (default 60)
- `PAPERBOT_PAUSED=true`: kill switch; prevents new worker processing until disabled

The worker writes rolling diagnostics (last 20 closed trades: win rate, net P&L and expectancy) to its logs. This is review, **not automatic strategy learning or auto-tuning**. It does not rewrite strategy rules after a loss. The SQLite file must live on a persistent disk; ordinary ephemeral container storage does not survive replacement/redeploy. Back up the database before changing infrastructure. If Binance returns an HTTP 451/403 or otherwise cannot be reached, the worker will keep retrying on its polling schedule but will not process fallback-exchange prices or advance the account. HTTP 451 often indicates a legal or regional restriction, but the status alone does not establish the exact cause. Do not try to bypass exchange access restrictions. If data has a gap or changes source, the session refuses to guess; investigate the cause before manually resetting stored state.

For a local dry run only:
```bash
python -m pip install .
PAPERBOT_DB_PATH=./paperbot.sqlite paperbot-worker
```
On Windows PowerShell, set environment variables using `$env:PAPERBOT_DB_PATH="./paperbot.sqlite"` before running `paperbot-worker`. The process is intended for a persistent worker host, not Streamlit Community Cloud's dashboard process.


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

# Fetch OKX perpetual candles and historical OKX funding rates for a cost-aware backtest
paperbot --okx --symbol BTCUSDT --interval 5m --limit 500
```

Completed candles are sorted and validated; duplicate timestamps, missing intervals, malformed OHLC data and failed requests stop the run with an explicit error. Downloaded completed candles are cached under `.cache/binance/`. A failed network request does not silently backtest stale cache data.

## Backtest your own candles
CSV columns required: `timestamp,open,high,low,close`; `volume` is optional.
```bash
paperbot --csv data/BTCUSDT_1h.csv --balance 1000
```

## Safety and limitations
- The news watcher refreshes public feeds; it does not modify its own source code or autonomously retrain a strategy.
- The Streamlit dashboard session is temporary. The separate background worker persists its paper account only when configured with a durable SQLite path, such as the Render persistent disk in render.yaml.
- The worker uses fixed strategy rules and only reports post-trade diagnostics; it does not automatically optimize parameters or claim to learn a profitable strategy.
- The backtest enforces a UTC-day loss circuit breaker and records when it closes a position and blocks new entries for the rest of that UTC day; this remains a simplified simulation, not an exchange risk engine.
- Funding is modeled using a fixed deterministic assumption by default; the `--okx` mode can use historical realized OKX funding rates for OKX candle backtests. OKX rates are exchange-specific and must not be presented as Binance funding history.
- Exchange-specific liquidation, mark-price divergence, order-book depth, partial fills, and exchange precision/minimum-notional rules are not fully modeled.
- Fees are approximated using a configurable flat rate and slippage is a simplified adverse-fill assumption; results are not exchange-grade accounting.
- No real orders, API credentials, or live trading. Any future execution adapter must be separate, opt-in, testnet-first and independently tested.
- Never interpret positive backtest results or news headlines as evidence of future profitability.

## Project layout
`src/paperbot/` contains market-data, news, models, strategies, risk, paper broker, incremental session engine, CLI and backtest code; `tests/` contains deterministic tests; `.github/` contains CI.

### Paper-backtest execution-cost assumptions

The paper broker applies configurable execution assumptions from `RiskConfig`:
- **Commission:** 0.04% per entry and exit by default.
- **Slippage:** 0.02% adverse price movement on each fill by default.
- **Funding:** a fixed 0.01% rate per 8-hour UTC funding event by default. Positive rates charge longs and credit shorts; negative rates reverse that direction.

Funding is applied at 00:00, 08:00, and 16:00 UTC when a position is open. This is a deterministic stress-test assumption, **not historical funding data**. The `--okx` command fetches public OKX perpetual candles and historical realized funding rates (available history is limited by OKX). The backtest fails explicitly if a position reaches a funding boundary without a supplied historical rate. OKX rates are not Binance rates, so keep candle and funding sources aligned. The model does not yet simulate order-book depth, exchange-specific liquidation, mark-price divergence, or every gap/partial-fill behavior. Paper results are not evidence of profitability.

## Experimental price-action strategy (not yet proven profitable)

The course `The Trading Industry Will Hate Me for This FREE 10+ Hour Course` by fxalexg discusses market structure, higher-timeframe context, areas of interest, break-and-retest entries, candle confirmation, confluence, risk planning, and patience. These are educational concepts, not evidence that a particular rule set has an edge.

The repository now includes a separate, rule-based `BreakRetestStrategy` candidate. It:
- waits for a candle close beyond a prior rolling high/low;
- looks for a later retest of that level within a limited number of candles;
- requires a confirming candle close and a trend filter using an EMA;
- emits signals only from the candle history supplied to it, with execution still simulated by the paper broker.

It is a deliberately simplified approximation. It does not yet implement full multi-timeframe structure, subjective supply/demand zones, or news/event filters. The existing EMA crossover remains the default; do not replace it with the new candidate unless out-of-sample results support that choice.

Try the experimental strategy on public data without credentials:
```bash
paperbot --okx --symbol BTCUSDT --interval 15m --limit 400 --strategy break-retest
paperbot --okx --symbol ETHUSDT --interval 1h --limit 400 --strategy break-retest
```

Compare the same dates, symbol, timeframe, balance, fees, slippage, and funding assumptions against `--strategy ema`. Use separate training and untouched test periods; report net returns, maximum drawdown, profit factor, win rate, trade count, and sensitivity to costs. A strategy that makes no trades or performs poorly should be reported honestly, not tuned until it fits the test period.

**Risk note:** The course summary includes extreme account-flipping examples. This bot intentionally does not adopt 35–100% account risk per trade. Its risk controls and paper-only design take precedence over aggressive examples or marketing claims.

### Compare strategy candidates on a chronological holdout

Use a historical CSV with enough rows for a meaningful test when possible. The CLI holds out the final 30% of candles; the earlier 70% is passed only as indicator warm-up, so it does not generate simulated trades or contribute to reported P&L.

```bash
paperbot --csv path/to/BTCUSDT_15m.csv --compare-strategies
paperbot --csv path/to/ETHUSDT_1h.csv --compare-strategies
```

The comparison reports return, max drawdown, trade count, win rate, net profit factor, and average net P&L per trade for both candidates using the same default risk and cost assumptions. It does not optimize parameters. One holdout is only a first screen: repeat the evaluation on multiple non-overlapping time windows and include realistic fees, slippage, funding, and sufficiently long market histories before drawing conclusions. A low trade count or no trades is a result to investigate, not a reason to weaken filters automatically.

For a stricter sequential check across multiple unseen windows, run:
```bash
paperbot --csv path/to/BTCUSDT_15m.csv --walk-forward --walk-forward-folds 3
```

This uses the first half of the series for warm-up, then evaluates both fixed strategies across the following sequential folds. Each fold resets the paper balance, and the strategy parameters are not optimized. It is a stability check rather than a fully optimized walk-forward system; use longer historical CSV data and inspect whether results are consistent across folds before drawing conclusions.



### Validation-selected strategy research (no live auto-promotion)

For a small, predeclared candidate grid, run a chronological validation-selection and untouched-test evaluation:

```bash
paperbot --okx --symbol BTCUSDT --interval 15m --limit 1000 --optimize-validate-test
paperbot --okx --symbol ETHUSDT --interval 1h --limit 1000 --optimize-validate-test
```

The command uses the first 50% of candles as indicator warm-up, ranks a small fixed candidate set on the next 25% by validation net return penalized for drawdown, and evaluates only the selected candidate on the final 25%. Candidates with fewer than five closed validation trades are ineligible. The report shows modeled fees/slippage and OKX historical funding when OKX is selected. Use sufficiently long data, multiple symbols/timeframes and cost-sensitivity checks; this is a research aid, not proof of an edge.

The final test period is not scored for rejected candidates, reducing the temptation to choose a strategy based on test results. **This command does not modify the worker strategy, clear a safety pause, or enable live orders.** A candidate should not be promoted unless performance is stable across several new periods after realistic costs and funding.


### Render worker market-data source

The Render worker is configured for `okx-only` paper research mode. This deliberately uses public OKX USDT perpetual-swap candles and does not request Binance or Bybit endpoints, avoiding repeated calls to endpoints that reject this deployment. Session results are **OKX-specific research, not Binance backtest results**. To use Binance data, confirm the deployment's eligibility with Binance and change the source mode only when Binance's endpoint is legitimately available to that environment. This is not a proxy or location workaround.
