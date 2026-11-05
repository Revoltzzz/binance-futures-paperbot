"""Personal AI market analyst with session-based personal risk settings."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from paperbot.ai_analyst import analyze_market, classify_news_sentiment
from paperbot.news import NewsError, fetch_latest_news
from paperbot.personal_profile import DEFAULT_PROFILE, normalise_profile
from paperbot.public_market_data import load_public_klines

st.set_page_config(page_title="AI Market Analyst", page_icon="🧠", layout="wide")
if "personal_trading_profile" not in st.session_state:
    st.session_state.personal_trading_profile = dict(DEFAULT_PROFILE)
profile = normalise_profile(st.session_state.personal_trading_profile)

st.title("🧠 Your Personal AI Market Analyst")
st.caption("Balanced risk profile · SOL-focused scalping by default · BTC / ETH / SOL / XRP")
st.warning("PAPER-ONLY / MANUAL APPROVAL. This page never places orders. Signals are deterministic research candidates, not financial advice.")
st.info(
    f"Your profile: **{profile['risk_mode']}** · {profile['preferred_symbol']} · "
    f"{profile['trading_style']} · {profile['risk_per_trade_pct']:.1f}% reference risk/trade · "
    f"{profile['minimum_risk_reward']:.2f}R minimum planned reward/risk. "
    "Edit it on My Trading Profile."
)

with st.sidebar:
    st.header("Your analysis settings")
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
    symbol = st.selectbox("Market", symbols, index=symbols.index(profile["preferred_symbol"]))
    modes = ["scalping", "intraday"]
    mode = st.radio("Trading style", modes, index=modes.index(profile["trading_style"]), horizontal=True)
    timeframe = st.selectbox("Chart timeframe", ["1m", "5m", "15m", "1h"], index=1 if mode == "scalping" else 2)
    limit = st.slider("Completed candles", 100, 1000, 400, 50)
    auto_refresh = st.checkbox("Auto-refresh market and news", value=True)
    refresh = st.button("Refresh market + news", use_container_width=True)

if auto_refresh:
    st_autorefresh(interval=60_000, key="ai_analyst_refresh")

if refresh:
    st.cache_data.clear()

@st.cache_data(ttl=30, show_spinner=False)
def market_data(market: str, interval: str, count: int):
    return load_public_klines(
        symbol=market, interval=interval, limit=count,
        cache_dir=Path(".cache/ai-analyst/candles"), okx_only=True,
    )

@st.cache_data(ttl=180, show_spinner=False)
def news_data():
    return fetch_latest_news(limit=40, cache_path=Path(".cache/ai-analyst/news.json"))

try:
    with st.spinner(f"Loading {symbol} {timeframe} perpetual candles…"):
        candles, provider = market_data(symbol, timeframe, limit)
    analysis = analyze_market(
        candles, symbol, timeframe, mode=mode,
        min_score=float(profile["minimum_setup_score"]),
    )
except Exception as exc:
    st.error(f"Analysis unavailable: {type(exc).__name__}: {exc}")
    st.info("The analyst currently uses OKX USDT perpetual public candles. It will not silently substitute spot data.")
    st.stop()

last = candles[-1]
age = max(0, int((datetime.now(timezone.utc).timestamp() * 1000 - last.timestamp) / 1000))
st.caption(f"Provider: {provider} · latest candle open: {datetime.fromtimestamp(last.timestamp / 1000, timezone.utc):%Y-%m-%d %H:%M UTC} · approx. age: {age}s")
if age > ({"1m":180,"5m":900,"15m":2700,"1h":10800}[timeframe]):
    st.error("Market data is stale. Do not act on this analysis.")
    st.stop()

c1,c2,c3,c4 = st.columns(4)
c1.metric("Last price", f"{analysis.last_price:,.6g}")
c2.metric("Trend", analysis.trend)
c3.metric("RSI (14)", "n/a" if analysis.rsi is None else f"{analysis.rsi:.1f}")
c4.metric("Setup score", f"{analysis.score:.1f}/6")
if analysis.signal == "LONG":
    st.success("LONG SETUP CANDIDATE — manual review required")
elif analysis.signal == "SHORT":
    st.error("SHORT SETUP CANDIDATE — manual review required")
else:
    st.info("NO TRADE — wait for better confirmation")

if analysis.signal != "NO TRADE":
    a,b,c,d = st.columns(4)
    a.metric("Entry zone", f"{analysis.entry_low:,.6g} – {analysis.entry_high:,.6g}")
    b.metric("Stop loss", f"{analysis.stop_loss:,.6g}")
    c.metric("Target 1", f"{analysis.target_1:,.6g}")
    d.metric("Target 2", f"{analysis.target_2:,.6g}", f"{analysis.risk_reward_2:.1f}R")
    if analysis.risk_reward_2 is not None and analysis.risk_reward_2 >= profile["minimum_risk_reward"]:
        risk_usdt = profile["reference_capital_usdt"] * profile["risk_per_trade_pct"] / 100
        stop_distance = abs(analysis.last_price - analysis.stop_loss)
        estimated_qty = risk_usdt / stop_distance if stop_distance else 0
        st.subheader("Your paper risk plan")
        r1,r2,r3 = st.columns(3)
        r1.metric("Risk budget", f"{risk_usdt:.2f} USDT")
        r2.metric("Estimated base quantity", f"{estimated_qty:.8f}")
        r3.metric("Estimated notional", f"{estimated_qty * analysis.last_price:.2f} USDT")
        st.caption("Approximate sizing only; contract multipliers, lot sizes, fees, slippage and funding are excluded. This is not an exchange order quantity.")
    else:
        st.warning("Setup does not meet your minimum reward/risk preference. Treat it as NO TRADE.")

with st.expander("Why the analyst reached this view", expanded=True):
    for reason in analysis.reasons:
        st.write("• " + reason)
    for warning in analysis.warnings:
        st.warning(warning)

# Build an interactive OHLCV chart so the user can inspect the actual setup.
import plotly.graph_objects as go
from plotly.subplots import make_subplots

chart_rows = [
    {
        "Time": datetime.fromtimestamp(x.timestamp / 1000, timezone.utc),
        "Open": x.open, "High": x.high, "Low": x.low,
        "Close": x.close, "Volume": x.volume,
    }
    for x in candles
]
chart = pd.DataFrame(chart_rows).set_index("Time")
chart["EMA 9"] = chart["Close"].ewm(span=9, adjust=False).mean()
chart["EMA 21"] = chart["Close"].ewm(span=21, adjust=False).mean()
chart["EMA 50"] = chart["Close"].ewm(span=50, adjust=False).mean()
typical = (chart["High"] + chart["Low"] + chart["Close"]) / 3
chart["VWAP (rolling 96)"] = (
    (typical * chart["Volume"]).rolling(96, min_periods=1).sum()
    / chart["Volume"].rolling(96, min_periods=1).sum().replace(0, float("nan"))
)

st.subheader("Live candlestick chart · market structure")
st.caption("Candles use completed OKX USDT-perpetual data. Horizontal levels are recent confirmed pivots; EMA/VWAP overlays are calculated from the displayed candle history.")
fig = make_subplots(
    rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.04,
    row_heights=[0.78, 0.22], specs=[[{"secondary_y": False}], [{"secondary_y": False}]],
)
fig.add_trace(go.Candlestick(
    x=chart.index, open=chart["Open"], high=chart["High"],
    low=chart["Low"], close=chart["Close"], name="Price",
), row=1, col=1)
for column, color in [
    ("EMA 9", "#00bcd4"), ("EMA 21", "#ff9800"),
    ("EMA 50", "#9c27b0"), ("VWAP (rolling 96)", "#fdd835"),
]:
    fig.add_trace(go.Scatter(
        x=chart.index, y=chart[column], mode="lines",
        name=column, line={"width": 1.5, "color": color},
    ), row=1, col=1)
fig.add_trace(go.Bar(
    x=chart.index, y=chart["Volume"], name="Volume",
    marker={"color": "#78909c"}, opacity=0.55,
), row=2, col=1)

for index, price in enumerate(analysis.support, start=1):
    fig.add_hline(y=price, line_dash="dot", line_color="#26a69a",
                  annotation_text=f"S{index} {price:,.6g}", row=1, col=1)
for index, price in enumerate(analysis.resistance, start=1):
    fig.add_hline(y=price, line_dash="dot", line_color="#ef5350",
                  annotation_text=f"R{index} {price:,.6g}", row=1, col=1)

if analysis.signal in {"LONG", "SHORT"}:
    fig.add_hrect(y0=analysis.entry_low, y1=analysis.entry_high,
                  fillcolor="#2196f3", opacity=0.12, line_width=0,
                  annotation_text="Entry zone", row=1, col=1)
    for label, price, color, dash in [
        ("Stop loss", analysis.stop_loss, "#ef5350", "dash"),
        ("Target 1", analysis.target_1, "#66bb6a", "dash"),
        ("Target 2", analysis.target_2, "#2e7d32", "dot"),
    ]:
        fig.add_hline(y=price, line_dash=dash, line_color=color,
                      annotation_text=f"{label} {price:,.6g}", row=1, col=1)
    fig.add_annotation(
        x=chart.index[-1], y=analysis.last_price,
        text=f"{analysis.signal} candidate · score {analysis.score:.1f}/6",
        showarrow=True, arrowhead=2, ax=35, ay=-45,
        bgcolor="#1b5e20" if analysis.signal == "LONG" else "#b71c1c",
        font={"color": "white"}, row=1, col=1,
    )

fig.update_layout(
    height=760, xaxis_rangeslider_visible=False,
    hovermode="x unified", legend={"orientation": "h", "y": 1.02},
    margin={"l": 10, "r": 10, "t": 35, "b": 10},
)
fig.update_yaxes(title_text="Price", row=1, col=1)
fig.update_yaxes(title_text="Volume", row=2, col=1)
st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": True, "displaylogo": False})

levels = [{"Type":"Support","Price":x} for x in analysis.support] + [{"Type":"Resistance","Price":x} for x in analysis.resistance]
if levels:
    st.dataframe(pd.DataFrame(levels), use_container_width=True, hide_index=True)
st.caption("Chart levels are technical estimates, not guaranteed reversal points. Confirm breakouts/retests and check liquidity before entering.")

tv_symbol = symbol.replace("USDT", "USDT.P")
tv_url = f"https://www.tradingview.com/chart/?symbol=OKX%3A{tv_symbol}"
st.link_button("Open this perpetual market in TradingView", tv_url, use_container_width=True)

st.divider()
st.subheader("Live crypto news intelligence")
try:
    headlines = news_data()
    relevant = []
    ticker = symbol.replace("USDT", "")
    for item in headlines:
        sentiment, score = classify_news_sentiment(item.title, item.summary)
        combined = (item.title + " " + item.summary).lower()
        keywords = {"BTC":("bitcoin","btc"),"ETH":("ethereum","ether","eth"),
                    "SOL":("solana","sol"),"XRP":("xrp","ripple")}[ticker]
        if any(word in combined for word in keywords) or ticker == "BTC":
            relevant.append((item, sentiment, score))
    for item, sentiment, score in relevant[:20]:
        with st.container(border=True):
            st.markdown(f"**{item.title}**")
            st.caption(f"{item.source} · {item.published_at or 'time unavailable'} · heuristic sentiment: {sentiment} ({score:+d})")
            st.markdown(f"[Read source]({item.url})")
    if not relevant:
        st.info("No asset-specific headlines found in the latest feed refresh.")
except NewsError as exc:
    st.warning(f"News refresh failed: {exc}. No cached headline is presented as fresh.")

st.caption("News sentiment is keyword-based and can misclassify sarcasm, context, or rumours. News is context only and never overrides technical/risk checks.")
