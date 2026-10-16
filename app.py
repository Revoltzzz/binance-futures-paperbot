"""Browser dashboard for the Binance Futures paper bot.

Deploy on Streamlit Community Cloud. This app only downloads public data and
runs local simulations; it never creates exchange orders.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from paperbot.backtest import run_backtest
from paperbot.binance_data import MarketDataError
from paperbot.models import Candle
from paperbot.session import PaperTradingSession
from paperbot.news import NewsError, NewsItem, fetch_latest_news
from paperbot.public_market_data import load_public_klines
from paperbot.strategy import BreakRetestStrategy, EmaCrossStrategy
from paperbot.storage import SQLitePaperStore
from paperbot.review import summarize_trades

ROOT = Path(".cache/web")
st.set_page_config(page_title="PaperBot Market Lab", page_icon="📈", layout="wide")

st.markdown(
    """
    <style>
    .block-container {max-width: 1400px; padding-top: 1.6rem;}
    .risk-note {padding: .8rem 1rem; border-left: 4px solid #d97706;
                background: rgba(217,119,6,.09); border-radius: .35rem;}
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("📈 PaperBot Market Lab")
st.caption("Public OKX perpetual-swap market data · live news headlines · simulated backtests")

st.markdown(
    '<div class="risk-note"><b>Paper trading only.</b> No exchange keys are used and '
    'no real orders can be placed. Backtests are historical simulations, not profit forecasts.</div>',
    unsafe_allow_html=True,
)


@st.cache_data(ttl=45, show_spinner=False)
def get_candles(symbol: str, interval: str, limit: int) -> tuple[list[Candle], str, list[str]]:
    diagnostics: list[str] = []
    candles, source = load_public_klines(
        symbol=symbol, interval=interval, limit=limit, cache_dir=ROOT / "candles",
        diagnostics=diagnostics, okx_only=True,
    )
    return candles, source, diagnostics


@st.cache_data(ttl=300, show_spinner=False)
def get_news(limit: int) -> list[NewsItem]:
    return fetch_latest_news(limit=limit, cache_path=ROOT / "news.json")


with st.sidebar:
    st.header("Market settings")
    symbol = st.selectbox("Futures market", ["BTCUSDT", "ETHUSDT"], index=0)
    interval = st.selectbox("Candle interval", ["5m", "15m", "1h"], index=0)
    limit = st.slider("Historical candles", min_value=100, max_value=1000, value=500, step=50)
    starting_balance = st.number_input("Starting paper balance (USDT)", min_value=100.0,
                                       max_value=10000.0, value=100.0, step=25.0)
    fast_period = st.slider("Fast EMA", min_value=2, max_value=10, value=5)
    slow_period = st.slider("Slow EMA", min_value=11, max_value=40, value=12)
    news_limit = st.slider("Headlines to show", min_value=5, max_value=30, value=15)
    auto_refresh = st.checkbox("Auto-refresh dashboard", value=True)
    refresh_seconds = st.select_slider("Refresh interval", options=[30, 60, 120, 300], value=60)
    if st.button("Refresh data now", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

if auto_refresh:
    st_autorefresh(interval=refresh_seconds * 1000, key="paperbot_refresh")

if slow_period <= fast_period:
    st.error("Slow EMA must be greater than Fast EMA.")
    st.stop()

market_error = None
candles: list[Candle] = []
market_source = ""
provider_diagnostics: list[str] = []
with st.spinner(f"Loading public {symbol} {interval} candles…"):
    try:
        candles, market_source, provider_diagnostics = get_candles(symbol, interval, limit)
    except (MarketDataError, OSError, ValueError) as exc:
        market_error = str(exc)

if market_error:
    st.error(f"Market data unavailable: {market_error}")
    st.info("The dashboard is configured to request OKX perpetual-swap data only. Binance/Bybit access restrictions are not retried or bypassed, and no stale candle cache is silently used. OKX will be retried on the next refresh.")
else:
    latest = candles[-1]
    now = datetime.now(timezone.utc)
    latest_dt = datetime.fromtimestamp(latest.timestamp / 1000, tz=timezone.utc)
    delay_seconds = max(0, int((now - latest_dt).total_seconds()))
    try:
        # Keep the provider impossible to miss: spot proxy results must not be
        # mistaken for Futures results when users inspect the performance cards.
        st.info(f"**Market data provider:** {market_source}")
        if provider_diagnostics:
            with st.expander("Why earlier providers failed"):
                st.caption("These are the actual public-data errors seen during this refresh. No account credentials are used.")
                for diagnostic in provider_diagnostics:
                    st.code(diagnostic, language=None)
        interval_seconds = {"5m": 300, "15m": 900, "1h": 3600}[interval]
        if delay_seconds > interval_seconds * 3:
            st.warning(
                f"Latest candle appears stale ({delay_seconds // 60} minutes old). "
                "Refresh the data and do not interpret this backtest as current-market evidence."
            )
        result = run_backtest(candles, float(starting_balance),
                              EmaCrossStrategy(fast_period=fast_period, slow_period=slow_period))
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Latest close", f"${latest.close:,.2f}")
        m2.metric("Paper return", f"{result.total_return_pct:+.2f}%")
        m3.metric("Ending balance", f"{result.ending_balance:,.2f} USDT")
        m4.metric("Closed trades", str(result.trades))
        m5.metric("Max drawdown", f"{result.max_drawdown_pct:.2f}%")
        st.caption(
            f"{symbol} · {interval} · {len(candles)} completed candles · "
            f"source: {market_source} · latest candle open {latest_dt:%Y-%m-%d %H:%M:%S} UTC · "
            f"approx. candle age {delay_seconds // 60} min"
        )
        if "Kraken spot proxy" in market_source:
            st.warning("Binance and Bybit futures data were unavailable. This is a SPOT-market proxy, not futures data; it excludes futures basis, funding, and contract-specific behavior. Do not use this run to validate a futures strategy.")
        elif "OKX USDT perpetual swap" in market_source:
            st.warning("This dashboard is configured to use OKX USDT perpetual-swap candles only because Binance/Bybit endpoints are blocked from some hosting regions. Contract specifications and funding behavior differ by exchange; these are exploratory OKX-based simulations, not Binance-specific results.")
        elif "Bybit USDT linear perpetual" in market_source:
            st.warning("Binance was unavailable, so this backtest uses Bybit linear-perpetual candles. Do not compare results across providers as if their candles were identical.")
        prices = pd.DataFrame({
            "Time (UTC)": [datetime.fromtimestamp(c.timestamp / 1000, tz=timezone.utc) for c in candles],
            "Close": [c.close for c in candles],
            "Volume": [c.volume for c in candles],
        }).set_index("Time (UTC)")
        left, right = st.columns([2, 1])
        with left:
            st.subheader("Price history")
            st.line_chart(prices[["Close"]], use_container_width=True)
        with right:
            st.subheader("Volume")
            st.bar_chart(prices[["Volume"]], use_container_width=True)

        st.subheader("Paper backtest summary")
        stats = st.columns(4)
        stats[0].metric("Starting balance", f"{result.starting_balance:,.2f} USDT")
        stats[1].metric("Wins / losses", f"{result.wins} / {result.losses}")
        stats[2].metric("EMA settings", f"{fast_period} / {slow_period}")
        stats[3].metric("Candles", str(len(candles)))
        st.subheader("Paper-trading journal")
        st.caption("This journal covers the current backtest run only. Download it before changing settings or refreshing; this dashboard does not yet provide durable cross-session storage.")
        if result.trade_history:
            journal = pd.DataFrame([{
                "Entry time (UTC)": item.entry_time_utc,
                "Exit time (UTC)": item.exit_time_utc,
                "Side": item.side.upper(),
                "Entry price": item.entry_price,
                "Exit price": item.exit_price,
                "Quantity": item.quantity,
                "Gross P&L (USDT)": item.gross_pnl,
                "Fees (USDT)": item.fees,
                "Net P&L (USDT)": item.net_pnl,
                "Exit reason": item.exit_reason,
                "Balance after (USDT)": item.balance_after,
            } for item in result.trade_history])
            st.dataframe(journal, use_container_width=True, hide_index=True)
            st.download_button(
                "Download trade journal (CSV)",
                data=journal.to_csv(index=False).encode("utf-8"),
                file_name=f"paperbot_{symbol}_{interval}_trade_journal.csv",
                mime="text/csv",
                use_container_width=True,
            )
        else:
            st.info("No closed trades in this backtest run, so the trade journal is empty.")

        if result.risk_events:
            st.subheader("Risk-limit events")
            risk_journal = pd.DataFrame([{
                "Time (UTC)": event.time_utc,
                "Event": event.event,
                "Marked equity (USDT)": event.marked_equity,
                "Balance after (USDT)": event.balance_after,
                "Details": event.message,
            } for event in result.risk_events])
            st.dataframe(risk_journal, use_container_width=True, hide_index=True)
            st.download_button(
                "Download risk events (CSV)",
                data=risk_journal.to_csv(index=False).encode("utf-8"),
                file_name=f"paperbot_{symbol}_{interval}_risk_events.csv",
                mime="text/csv",
                use_container_width=True,
            )
        else:
            st.caption("No daily-loss circuit-breaker events were recorded in this run.")

        st.caption(
            "The current strategy is a basic EMA-crossover baseline. Results exclude funding, "
            "liquidations, exchange precision rules and realistic market impact; treat them as exploratory only."
        )
        with st.expander("Inspect recent candle data"):
            st.dataframe(prices.tail(100).sort_index(ascending=False), use_container_width=True)

        st.divider()
        st.subheader("Strategy lab")
        st.caption(
            "Compare the existing EMA baseline with the experimental break-and-retest candidate. "
            "These are historical paper simulations, not live signals or profit forecasts."
        )
        evaluation_mode = st.selectbox(
            "Evaluation mode",
            [
                "Single-strategy backtest",
                "Compare strategies (70/30 holdout)",
                "Walk-forward (3 sequential folds)",
            ],
            key="strategy_lab_evaluation_mode",
        )

        if evaluation_mode == "Single-strategy backtest":
            candidate_label = st.selectbox(
                "Strategy candidate",
                ["EMA crossover", "Break-and-retest"],
                key="strategy_lab_candidate",
            )
            candidate = (
                EmaCrossStrategy(fast_period=fast_period, slow_period=slow_period)
                if candidate_label == "EMA crossover"
                else BreakRetestStrategy()
            )
            lab_result = run_backtest(candles, float(starting_balance), candidate)
            lab_metrics = st.columns(5)
            lab_metrics[0].metric("Return", f"{lab_result.total_return_pct:+.2f}%")
            lab_metrics[1].metric("Ending balance", f"{lab_result.ending_balance:,.2f} USDT")
            lab_metrics[2].metric("Closed trades", str(lab_result.trades))
            lab_metrics[3].metric("Max drawdown", f"{lab_result.max_drawdown_pct:.2f}%")
            lab_metrics[4].metric(
                "Profit factor",
                "n/a" if lab_result.profit_factor is None else f"{lab_result.profit_factor:.2f}",
            )
            st.caption(
                f"{candidate_label} · {lab_result.wins} wins / {lab_result.losses} losses · "
                f"average net P&L per trade {lab_result.expectancy_per_trade:.4f} USDT"
            )

        elif evaluation_mode == "Compare strategies (70/30 holdout)":
            split_index = int(len(candles) * 0.7)
            if split_index < 20 or len(candles) - split_index < 2:
                st.warning("Not enough candles for this comparison. Load more historical candles.")
            else:
                warmup_candles = candles[:split_index]
                test_candles = candles[split_index:]
                candidates = [
                    ("EMA crossover", EmaCrossStrategy(fast_period=fast_period, slow_period=slow_period)),
                    ("Break-and-retest", BreakRetestStrategy()),
                ]
                comparison_rows = []
                for candidate_name, candidate in candidates:
                    candidate_result = run_backtest(
                        test_candles,
                        float(starting_balance),
                        strategy=candidate,
                        warmup_candles=warmup_candles,
                    )
                    comparison_rows.append({
                        "Strategy": candidate_name,
                        "Return (%)": round(candidate_result.total_return_pct, 3),
                        "Max drawdown (%)": round(candidate_result.max_drawdown_pct, 3),
                        "Trades": candidate_result.trades,
                        "Win rate (%)": round(
                            candidate_result.wins / candidate_result.trades * 100, 2
                        ) if candidate_result.trades else 0.0,
                        "Profit factor": (
                            "n/a" if candidate_result.profit_factor is None
                            else round(candidate_result.profit_factor, 3)
                        ),
                        "Expectancy (USDT/trade)": round(candidate_result.expectancy_per_trade, 4),
                    })
                st.dataframe(pd.DataFrame(comparison_rows), use_container_width=True, hide_index=True)
                st.caption(
                    f"Chronological holdout: first {split_index} candles are warm-up only; "
                    f"the final {len(test_candles)} candles are evaluated. Parameters are not optimized."
                )

        else:
            fold_count = 3
            first_test_index = len(candles) // 2
            remaining = len(candles) - first_test_index
            fold_size = remaining // fold_count
            if first_test_index < 50 or fold_size < 10:
                st.warning("Walk-forward evaluation needs at least 50 warm-up candles and 10 candles per test fold.")
            else:
                candidates = [
                    ("EMA crossover", EmaCrossStrategy(fast_period=fast_period, slow_period=slow_period)),
                    ("Break-and-retest", BreakRetestStrategy()),
                ]
                fold_rows = []
                for fold_index in range(fold_count):
                    start_index = first_test_index + fold_index * fold_size
                    end_index = (
                        len(candles) if fold_index == fold_count - 1
                        else start_index + fold_size
                    )
                    fold_warmup = candles[:start_index]
                    fold_candles = candles[start_index:end_index]
                    for candidate_name, candidate in candidates:
                        fold_result = run_backtest(
                            fold_candles,
                            float(starting_balance),
                            strategy=candidate,
                            warmup_candles=fold_warmup,
                        )
                        fold_rows.append({
                            "Fold": fold_index + 1,
                            "Strategy": candidate_name,
                            "Return (%)": round(fold_result.total_return_pct, 3),
                            "Max drawdown (%)": round(fold_result.max_drawdown_pct, 3),
                            "Trades": fold_result.trades,
                            "Win rate (%)": round(
                                fold_result.wins / fold_result.trades * 100, 2
                            ) if fold_result.trades else 0.0,
                            "Profit factor": (
                                "n/a" if fold_result.profit_factor is None
                                else round(fold_result.profit_factor, 3)
                            ),
                            "Expectancy (USDT/trade)": round(fold_result.expectancy_per_trade, 4),
                        })
                st.dataframe(pd.DataFrame(fold_rows), use_container_width=True, hide_index=True)
                st.caption(
                    "Each fold resets the paper balance; earlier candles are warm-up only. "
                    "Compare consistency across folds rather than adding returns together. "
                    "This is a stability check, not proof of a profitable edge."
                )
        st.divider()
        st.subheader("Incremental paper-trading session")
        st.caption(
            "Start a separate virtual account that processes each newly completed candle once. "
            "It uses the selected public market-data provider, simulated fills, stop-loss/take-profit, "
            "modeled fees/slippage/funding, and a daily-loss circuit breaker. It never places exchange orders. "
            "Session state is limited to this browser session and can be lost on server restart."
        )
        session_strategy_label = st.selectbox(
            "Paper-session strategy",
            ["EMA crossover", "Break-and-retest"],
            key="paper_session_strategy",
        )
        if st.button("Start / reset paper session", key="start_paper_session", use_container_width=True):
            session_strategy_name = "ema" if session_strategy_label == "EMA crossover" else "break-retest"
            st.session_state["paperbot_live_session"] = PaperTradingSession(
                symbol=symbol,
                interval=interval,
                market_source=market_source,
                starting_balance=float(starting_balance),
                strategy_name=session_strategy_name,
                fast_period=fast_period,
                slow_period=slow_period,
                initial_candles=candles,
            )
            st.success("Paper session initialized. It will wait for the next newly completed candle before generating its first session trade.")

        paper_session = st.session_state.get("paperbot_live_session")
        if paper_session is not None:
            settings_match = paper_session.symbol == symbol and paper_session.interval == interval
            if not settings_match:
                st.warning(
                    f"This session is for {paper_session.symbol} {paper_session.interval}. "
                    "Restore those market settings or start/reset the session."
                )
                session_mark = None
            else:
                processed_count = paper_session.process_new_candles(candles, market_source)
                if paper_session.paused_reason:
                    st.warning(f"Paper session paused: {paper_session.paused_reason}")
                    session_mark = None
                else:
                    if processed_count:
                        st.success(f"Processed {processed_count} new completed candle(s).")
                    else:
                        st.info("Paper session is active and waiting for the next completed candle.")
                    session_mark = latest.close

            session_equity = (
                paper_session.broker.equity(session_mark)
                if session_mark is not None else paper_session.broker.equity()
            )
            session_position = paper_session.broker.position
            sm1, sm2, sm3, sm4, sm5 = st.columns(5)
            sm1.metric("Session balance", f"{paper_session.broker.balance:,.2f} USDT")
            sm2.metric("Marked equity", f"{session_equity:,.2f} USDT")
            sm3.metric(
                "Open position",
                "Flat" if session_position is None else session_position.side.value.upper(),
            )
            sm4.metric("Closed session trades", str(len(paper_session.trades)))
            sm5.metric("Daily loss guard", "HALTED" if paper_session.daily_halted else "Armed")

            if session_position is not None:
                st.caption(
                    f"Open {session_position.side.value.upper()} · entry "
                    f"{session_position.entry_price:,.4f} · stop {session_position.stop_price:,.4f} · "
                    f"target {session_position.take_profit_price:,.4f}"
                )

            if paper_session.events:
                st.subheader("Paper-session risk events")
                st.dataframe(
                    pd.DataFrame(paper_session.events[-100:]),
                    use_container_width=True,
                    hide_index=True,
                )
            if paper_session.trades:
                st.subheader("Incremental paper-session journal")
                session_journal = pd.DataFrame(paper_session.trades)
                st.dataframe(
                    session_journal.tail(100),
                    use_container_width=True,
                    hide_index=True,
                )
                st.download_button(
                    "Download session journal (CSV)",
                    data=session_journal.to_csv(index=False).encode("utf-8"),
                    file_name=f"paper_session_{paper_session.symbol}_{paper_session.interval}.csv",
                    mime="text/csv",
                    key="download_paper_session_journal",
                    use_container_width=True,
                )
            else:
                st.caption(
                    "No session trades have closed yet. The historical backtest above is separate "
                    "from this session account."
                )

    except (ValueError, ArithmeticError) as exc:
        st.error(f"Could not calculate the backtest: {exc}")

st.divider()
st.subheader("Latest crypto-market news")
st.caption("Headlines are context, not verified facts or buy/sell instructions. Sources may be delayed or unavailable.")
try:
    news_items = get_news(news_limit)
    if not news_items:
        st.info("No usable headlines were returned.")
    for item in news_items:
        title = item.title.replace("\n", " ").strip()
        published = item.published_at or "publication time unavailable"
        st.markdown(f"**[{title}]({item.url})**")
        st.caption(f"{item.source} · {published}")
        if item.summary:
            summary = " ".join(item.summary.split())
            if summary:
                st.write(summary[:350] + ("…" if len(summary) > 350 else ""))
        st.markdown("")
except (NewsError, OSError, ValueError) as exc:
    st.warning(f"News feeds are temporarily unavailable: {exc}")

st.caption("Data is fetched from public endpoints when available. A refresh does not imply every global market or news source is covered.")


st.divider()
st.header("Persistent worker account")
st.caption(
    "Read-only view of the background worker's durable SQLite account. "
    "To show the hosted worker's actual account, this dashboard must run in the same "
    "environment with access to its persistent database; a separate Streamlit Cloud "
    "instance cannot read the Render worker's private disk."
)
worker_db = Path(os.getenv("PAPERBOT_DB_PATH", "/var/data/paperbot.sqlite"))
if not worker_db.is_file():
    st.info(
        f"No worker database is mounted at {worker_db}. The market lab above remains "
        "available. Set PAPERBOT_DB_PATH to an existing paperbot.sqlite file to view "
        "persisted worker sessions; this section never initializes or resets account data."
    )
else:
    try:
        worker_store = SQLitePaperStore(worker_db)
        worker_sessions = worker_store.list_sessions()
        if not worker_sessions:
            st.info("The worker database exists, but it contains no paper sessions yet.")
        else:
            selected_session_id = st.selectbox(
                "Persisted worker session",
                [row["session_id"] for row in worker_sessions],
                format_func=lambda session_id: next(
                    (f"{session_id} · revision {row['revision']} · updated {row['updated_at']}"
                     for row in worker_sessions if row["session_id"] == session_id),
                    session_id,
                ),
                key="persisted_worker_session",
            )
            loaded_worker = worker_store.load(selected_session_id)
            if loaded_worker is None:
                st.warning("This session changed while the dashboard was loading. Refresh to retry.")
            else:
                worker_revision, worker_state = loaded_worker
                worker_broker = worker_state.get("broker", {})
                worker_position = worker_broker.get("position")
                worker_journal = worker_state.get("trades", [])
                worker_balance = float(worker_broker.get("balance", 0.0))
                worker_starting = float(worker_state.get("starting_balance", worker_balance))
                worker_net = worker_balance - worker_starting
                worker_equity_cols = st.columns(4)
                worker_equity_cols[0].metric("Persisted balance", f"{worker_balance:,.2f} USDT")
                worker_equity_cols[1].metric("Net balance change", f"{worker_net:+,.2f} USDT")
                worker_equity_cols[2].metric("Closed trades", str(len(worker_journal)))
                worker_equity_cols[3].metric("Snapshot revision", str(worker_revision))
                st.caption(
                    f"{worker_state.get('symbol', 'Unknown symbol')} · "
                    f"{worker_state.get('interval', 'Unknown interval')} · "
                    f"Source: {worker_state.get('market_source', 'unknown')} · "
                    f"Strategy: {worker_state.get('strategy_name', 'unknown')}"
                )
                if worker_state.get("paused_reason"):
                    st.warning(f"Worker paused: {worker_state['paused_reason']}")
                elif worker_state.get("daily_halted"):
                    st.warning("Daily-loss guard is active.")
                else:
                    st.success("No persisted pause reason is recorded in this snapshot.")
                if worker_position:
                    st.subheader("Open simulated position")
                    st.dataframe(pd.DataFrame([{
                        "Side": worker_position.get("side"),
                        "Quantity": worker_position.get("quantity"),
                        "Entry price": worker_position.get("entry_price"),
                        "Stop loss": worker_position.get("stop_price"),
                        "Take profit": worker_position.get("take_profit_price"),
                        "Entry fee": worker_position.get("entry_fee"),
                        "Funding cost": worker_position.get("funding_cost"),
                    }]), use_container_width=True, hide_index=True)
                else:
                    st.caption("No open simulated position in this saved snapshot.")
                if worker_journal:
                    worker_trades_df = pd.DataFrame(worker_journal)
                    worker_review = summarize_trades(worker_journal, window=max(1, len(worker_journal)))
                    worker_metric_cols = st.columns(4)
                    worker_metric_cols[0].metric(
                        "Win rate", "n/a" if worker_review["win_rate_pct"] is None
                        else f"{worker_review['win_rate_pct']:.1f}%"
                    )
                    worker_metric_cols[1].metric(
                        "Trade net P&L", f"{worker_review['net_pnl']:+.2f} USDT"
                    )
                    worker_metric_cols[2].metric(
                        "Expectancy / trade",
                        "n/a" if worker_review["expectancy_per_trade"] is None
                        else f"{worker_review['expectancy_per_trade']:+.4f} USDT"
                    )
                    worker_metric_cols[3].metric(
                        "Profit factor",
                        "n/a" if worker_review["profit_factor"] is None
                        else f"{worker_review['profit_factor']:.2f}"
                    )
                    st.subheader("Persisted trade history")
                    st.dataframe(worker_trades_df.iloc[::-1], use_container_width=True, hide_index=True)
                    st.download_button(
                        "Export worker trades (CSV)",
                        data=worker_trades_df.to_csv(index=False).encode("utf-8"),
                        file_name=f"worker_{selected_session_id.replace(':', '_')}_trades.csv",
                        mime="text/csv",
                    )
                    if "Net P&L (USDT)" in worker_trades_df:
                        worker_trades_df["Cumulative net P&L (USDT)"] = (
                            pd.to_numeric(worker_trades_df["Net P&L (USDT)"], errors="coerce")
                            .fillna(0).cumsum()
                        )
                        st.line_chart(
                            worker_trades_df.set_index("Exit time (UTC)")[
                                ["Cumulative net P&L (USDT)"]
                            ]
                        )
                else:
                    st.info("No closed worker trades have been recorded in this session yet.")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        st.error(f"Could not read the persisted worker snapshot safely: {exc}")

st.divider()
st.header("Bybit Demo Trading connection")
st.caption(
    "Connection check only: reads the demo wallet and cannot submit orders. "
    "Create API credentials while Bybit Demo Trading mode is active, then configure "
    "BYBIT_DEMO_API_KEY and BYBIT_DEMO_API_SECRET privately in the dashboard environment."
)
if st.button("Check Bybit demo wallet", key="check_bybit_demo_wallet"):
    try:
        from paperbot.bybit_demo import BybitDemoError, get_wallet_balance
        with st.spinner("Checking Bybit Demo Trading API…"):
            demo_wallet = get_wallet_balance()
        st.success("Bybit Demo Trading API authenticated successfully.")
        st.json(demo_wallet.get("result", {}))
    except Exception as exc:
        st.error(f"Bybit demo wallet check failed: {exc}")


if st.button("Check OKX Demo Trading wallet", key="check_okx_demo_wallet"):
    try:
        from paperbot.okx_demo import get_balance
        with st.spinner("Checking OKX Demo Trading API…"):
            demo_balance = get_balance()
        st.success("OKX Demo Trading API authenticated successfully.")
        st.json(demo_balance.get("data", []))
    except Exception as exc:
        st.error(f"OKX demo wallet check failed: {exc}")
