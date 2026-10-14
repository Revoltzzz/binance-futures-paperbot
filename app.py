"""Browser dashboard for the Binance Futures paper bot.

Deploy on Streamlit Community Cloud. This app only downloads public data and
runs local simulations; it never creates exchange orders.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from paperbot.backtest import run_backtest
from paperbot.binance_data import MarketDataError
from paperbot.models import Candle
from paperbot.news import NewsError, NewsItem, fetch_latest_news
from paperbot.public_market_data import load_public_klines
from paperbot.strategy import EmaCrossStrategy

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
st.caption("Public crypto market data · live news headlines · simulated backtests")

st.markdown(
    '<div class="risk-note"><b>Paper trading only.</b> No exchange keys are used and '
    'no real orders can be placed. Backtests are historical simulations, not profit forecasts.</div>',
    unsafe_allow_html=True,
)


@st.cache_data(ttl=45, show_spinner=False)
def get_candles(symbol: str, interval: str, limit: int) -> tuple[list[Candle], str]:
    return load_public_klines(
        symbol=symbol, interval=interval, limit=limit, cache_dir=ROOT / "candles"
    )


@st.cache_data(ttl=300, show_spinner=False)
def get_news(limit: int) -> list[NewsItem]:
    return fetch_latest_news(limit=limit, cache_path=ROOT / "news.json")


with st.sidebar:
    st.header("Market settings")
    symbol = st.selectbox("Futures market", ["BTCUSDT", "ETHUSDT"], index=0)
    interval = st.selectbox("Candle interval", ["5m", "15m", "1h"], index=0)
    limit = st.slider("Historical candles", min_value=100, max_value=1000, value=500, step=50)
    starting_balance = st.number_input("Starting paper balance (USDT)", min_value=100.0,
                                       max_value=10000.0, value=1000.0, step=100.0)
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
with st.spinner(f"Loading public {symbol} {interval} candles…"):
    try:
        candles, market_source = get_candles(symbol, interval, limit)
    except (MarketDataError, OSError, ValueError) as exc:
        market_error = str(exc)

if market_error:
    st.error(f"Market data unavailable: {market_error}")
    st.info("All configured public providers will be retried on the next refresh. No stale candle cache is silently used.")
else:
    latest = candles[-1]
    now = datetime.now(timezone.utc)
    latest_dt = datetime.fromtimestamp(latest.timestamp / 1000, tz=timezone.utc)
    delay_seconds = max(0, int((now - latest_dt).total_seconds()))
    try:
        # Keep the provider impossible to miss: spot proxy results must not be
        # mistaken for Futures results when users inspect the performance cards.
        st.info(f"**Market data provider:** {market_source}")
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
        elif "fallback" in market_source:
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
        st.caption(
            "The current strategy is a basic EMA-crossover baseline. Results exclude funding, "
            "liquidations, exchange precision rules and realistic market impact; treat them as exploratory only."
        )
        with st.expander("Inspect recent candle data"):
            st.dataframe(prices.tail(100).sort_index(ascending=False), use_container_width=True)
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
