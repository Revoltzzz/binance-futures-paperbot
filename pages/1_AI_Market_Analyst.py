"""Personal chart analyst with multi-timeframe confirmation and manual signals."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
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

st.title("🧠 Personal AI Chart Analyst")
st.caption("SOL-first scalping · multi-timeframe confirmation · chart levels · manual execution only")
st.warning(
    "ANALYSIS ONLY — this dashboard does not connect to your Binance wallet, read your balance, "
    "or place/cancel orders. Treat every setup as a candidate and verify it yourself."
)
st.info(
    f"Profile: **{profile['preferred_symbol']}** · {profile['trading_style']} · "
    f"{profile['risk_per_trade_pct']:.1f}% reference risk/trade · "
    f"{profile['minimum_risk_reward']:.2f}R minimum planned reward/risk. "
    "The reference capital is a calculator input, not your verified Binance balance."
)

with st.sidebar:
    st.header("Analysis controls")
    symbols = ["SOLUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"]
    symbol = st.selectbox("Market", symbols, index=symbols.index(profile["preferred_symbol"]))
    modes = ["scalping", "intraday"]
    mode = st.radio("Trading style", modes, index=modes.index(profile["trading_style"]), horizontal=True)
    timeframe = st.selectbox("Signal timeframe", ["1m", "5m", "15m", "1h"], index=1 if mode == "scalping" else 2)
    limit = st.slider("Candles on chart", 100, 1000, 400, 50)
    auto_refresh = st.checkbox("Auto-refresh every minute", value=True)
    refresh = st.button("Refresh now", use_container_width=True)

if auto_refresh:
    st_autorefresh(interval=60_000, key="personal_analyst_refresh")
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

# Higher-timeframe checks reduce counter-trend signals. The 1h chart is the
# highest supported OKX timeframe in this analyst.
confirmation_map = {"1m": ["5m", "15m"], "5m": ["15m", "1h"], "15m": ["1h"], "1h": []}
try:
    with st.spinner(f"Loading {symbol} {timeframe} perpetual candles and confirmation timeframes…"):
        candles, provider = market_data(symbol, timeframe, limit)
        confirmations = {}
        for higher_tf in confirmation_map[timeframe]:
            higher_candles, higher_provider = market_data(symbol, higher_tf, max(120, min(limit, 400)))
            confirmations[higher_tf] = (
                analyze_market(higher_candles, symbol, higher_tf, mode="intraday", min_score=3.0),
                higher_candles[-1].timestamp,
                higher_provider,
            )
    analysis = analyze_market(
        candles, symbol, timeframe, mode=mode,
        min_score=float(profile["minimum_setup_score"]),
    )
except Exception as exc:
    st.error(f"Analysis unavailable: {type(exc).__name__}: {exc}")
    st.info("This analyst uses OKX USDT-margined perpetual public candles only. It will not silently substitute spot data.")
    st.stop()

now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
age = max(0, int((now_ms - candles[-1].timestamp) / 1000))
st.caption(
    f"Provider: {provider} · last candle open: "
    f"{datetime.fromtimestamp(candles[-1].timestamp / 1000, timezone.utc):%Y-%m-%d %H:%M UTC} · "
    f"approx. age: {age}s"
)
max_age = {"1m": 180, "5m": 900, "15m": 2700, "1h": 10800}[timeframe]
if age > max_age:
    st.error("Main timeframe data is stale. Do not use this analysis.")
    st.stop()

st.subheader("Higher-timeframe confirmation")
confirmation_rows = []
opposing = []
for higher_tf, (higher_analysis, timestamp, higher_provider) in confirmations.items():
    higher_age = max(0, int((now_ms - timestamp) / 1000))
    allowed_age = {"5m": 900, "15m": 2700, "1h": 10800}[higher_tf]
    fresh = higher_age <= allowed_age
    confirmation_rows.append({
        "Timeframe": higher_tf,
        "Trend": higher_analysis.trend,
        "Bias": higher_analysis.signal,
        "Data": "Fresh" if fresh else f"Stale ({higher_age}s)",
        "Provider": higher_provider,
    })
    if fresh and ((analysis.signal == "LONG" and higher_analysis.trend == "BEARISH") or
                  (analysis.signal == "SHORT" and higher_analysis.trend == "BULLISH")):
        opposing.append(higher_tf)
if confirmation_rows:
    st.dataframe(pd.DataFrame(confirmation_rows), use_container_width=True, hide_index=True)
else:
    st.caption("1h is the highest supported chart timeframe; no higher timeframe is available for confirmation.")
effective_signal = analysis.signal
if opposing:
    effective_signal = "NO TRADE"
    st.warning("Signal blocked: higher timeframe trend conflicts on " + ", ".join(opposing) + ". Wait for alignment.")
if any(row["Data"] != "Fresh" for row in confirmation_rows):
    effective_signal = "NO TRADE"
    st.warning("Signal blocked because a confirmation timeframe is stale.")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Last price", f"{analysis.last_price:,.6g}")
m2.metric("Trend", analysis.trend)
m3.metric("RSI (14)", "n/a" if analysis.rsi is None else f"{analysis.rsi:.1f}")
m4.metric("Confluence score", f"{analysis.score:.1f}/6")
if effective_signal == "LONG":
    st.success("🟢 LONG SETUP CANDIDATE — check the chart and approve manually")
elif effective_signal == "SHORT":
    st.error("🔴 SHORT SETUP CANDIDATE — check the chart and approve manually")
else:
    st.info("⏸️ NO TRADE — wait for cleaner structure or timeframe alignment")

if effective_signal in {"LONG", "SHORT"} and analysis.entry_low is not None:
    a, b, c, d = st.columns(4)
    a.metric("Entry zone", f"{analysis.entry_low:,.6g} – {analysis.entry_high:,.6g}")
    b.metric("Stop loss", f"{analysis.stop_loss:,.6g}")
    c.metric("Target 1", f"{analysis.target_1:,.6g}")
    d.metric("Target 2", f"{analysis.target_2:,.6g}", f"{analysis.risk_reward_2:.1f}R")
    if analysis.risk_reward_2 >= profile["minimum_risk_reward"]:
        risk_usdt = profile["reference_capital_usdt"] * profile["risk_per_trade_pct"] / 100
        stop_distance = abs(analysis.last_price - analysis.stop_loss)
        estimated_qty = risk_usdt / stop_distance if stop_distance else 0
        r1, r2, r3 = st.columns(3)
        r1.metric("Reference risk budget", f"{risk_usdt:.2f} USDT")
        r2.metric("Estimated base quantity", f"{estimated_qty:.8f}")
        r3.metric("Estimated notional", f"{estimated_qty * analysis.last_price:.2f} USDT")
        st.caption("Calculator estimate only; excludes contract multipliers, lot sizes, fees, slippage, funding and liquidation/margin rules. It is not an order quantity.")
    else:
        st.warning("Setup fails your minimum reward/risk preference. Treat it as NO TRADE.")

with st.expander("Why this signal was generated", expanded=True):
    for reason in analysis.reasons:
        st.write("• " + reason)
    for warning in analysis.warnings:
        st.warning(warning)

# Full candlestick chart with programmatically drawn indicators and structure.
frame = pd.DataFrame({
    "time": [datetime.fromtimestamp(x.timestamp / 1000, timezone.utc) for x in candles],
    "open": [x.open for x in candles],
    "high": [x.high for x in candles],
    "low": [x.low for x in candles],
    "close": [x.close for x in candles],
    "volume": [x.volume for x in candles],
})
frame["EMA 9"] = frame["close"].ewm(span=9, adjust=False).mean()
frame["EMA 21"] = frame["close"].ewm(span=21, adjust=False).mean()
frame["EMA 50"] = frame["close"].ewm(span=50, adjust=False).mean()
typical = (frame["high"] + frame["low"] + frame["close"]) / 3
frame["VWAP (rolling 96)"] = (typical * frame["volume"]).rolling(96, min_periods=1).sum() / frame["volume"].rolling(96, min_periods=1).sum().replace(0, float("nan"))

fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.04,
                    row_heights=[0.78, 0.22], specs=[[{"secondary_y": False}], [{"secondary_y": False}]])
fig.add_trace(go.Candlestick(
    x=frame["time"], open=frame["open"], high=frame["high"], low=frame["low"], close=frame["close"],
    name="Perpetual candles", increasing_line_color="#16a34a", decreasing_line_color="#dc2626",
), row=1, col=1)
for column, color in [("EMA 9", "#06b6d4"), ("EMA 21", "#f59e0b"), ("EMA 50", "#8b5cf6"), ("VWAP (rolling 96)", "#eab308")]:
    fig.add_trace(go.Scatter(x=frame["time"], y=frame[column], name=column, mode="lines",
                             line={"width": 1.4, "color": color}), row=1, col=1)
fig.add_trace(go.Bar(x=frame["time"], y=frame["volume"], name="Volume", marker_color="#64748b", opacity=0.45), row=2, col=1)

for level in analysis.support:
    fig.add_hline(y=level, line_dash="dot", line_color="#16a34a", opacity=0.8,
                  annotation_text=f"Support {level:.6g}", row=1, col=1)
for level in analysis.resistance:
    fig.add_hline(y=level, line_dash="dot", line_color="#dc2626", opacity=0.8,
                  annotation_text=f"Resistance {level:.6g}", row=1, col=1)

# Draw simple confirmed swing trendlines from the latest two pivot highs/lows.
radius = 3
sample = candles[-160:]
pivot_lows, pivot_highs = [], []
for i in range(radius, len(sample) - radius):
    neighbors = sample[i-radius:i] + sample[i+1:i+radius+1]
    if all(sample[i].low < x.low for x in neighbors):
        pivot_lows.append((i, sample[i].low))
    if all(sample[i].high > x.high for x in neighbors):
        pivot_highs.append((i, sample[i].high))
offset = len(candles) - len(sample)
for pivots, label, color in [(pivot_lows, "Rising/falling swing-low trendline", "#22c55e"),
                             (pivot_highs, "Swing-high trendline", "#ef4444")]:
    if len(pivots) >= 2:
        first, second = pivots[-2], pivots[-1]
        x0 = frame["time"].iloc[offset + first[0]]
        x1 = frame["time"].iloc[-1]
        fig.add_shape(type="line", x0=x0, y0=first[1], x1=x1, y1=second[1],
                      line={"color": color, "width": 2, "dash": "dash"}, row=1, col=1)
        fig.add_annotation(x=x1, y=second[1], text=label, showarrow=False,
                           font={"size": 9, "color": color}, xanchor="right", row=1, col=1)

if effective_signal in {"LONG", "SHORT"}:
    for label, price, color, dash in [
        ("Entry", (analysis.entry_low + analysis.entry_high) / 2, "#2563eb", "solid"),
        ("Stop", analysis.stop_loss, "#dc2626", "dash"),
        ("Target 1", analysis.target_1, "#16a34a", "dash"),
        ("Target 2", analysis.target_2, "#15803d", "dot"),
    ]:
        fig.add_hline(y=price, line_dash=dash, line_color=color, line_width=2,
                      annotation_text=f"{label} {price:.6g}", row=1, col=1)
fig.update_layout(
    height=780, xaxis_rangeslider_visible=False, template="plotly_dark",
    legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
    margin={"l": 20, "r": 20, "t": 50, "b": 20},
)
fig.update_yaxes(title_text="Price", row=1, col=1)
fig.update_yaxes(title_text="Volume", row=2, col=1)
st.subheader(f"{symbol} · {timeframe} annotated chart")
st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": True, "displaylogo": False})
st.caption("Green/red dotted lines are pivot support/resistance; dashed diagonals are recent swing trendlines. Indicators and levels are algorithmic estimates, not guaranteed support/resistance.")

tv_symbol = symbol.replace("USDT", "USDT.P")
tv_url = f"https://www.tradingview.com/chart/?symbol=OKX%3A{tv_symbol}"
st.link_button("Open same perpetual market on TradingView", tv_url, use_container_width=True)

st.divider()
st.subheader("Crypto news context")
try:
    headlines = news_data()
    ticker = symbol.replace("USDT", "")
    keywords = {"BTC": ("bitcoin", "btc"), "ETH": ("ethereum", "ether", "eth"),
                "SOL": ("solana", "sol"), "XRP": ("xrp", "ripple")}[ticker]
    relevant = []
    for item in headlines:
        sentiment, score = classify_news_sentiment(item.title, item.summary)
        combined = (item.title + " " + item.summary).lower()
        if any(word in combined for word in keywords) or ticker == "BTC":
            relevant.append((item, sentiment, score))
    for item, sentiment, score in relevant[:15]:
        with st.container(border=True):
            st.markdown(f"**{item.title}**")
            st.caption(f"{item.source} · {item.published_at or 'time unavailable'} · heuristic sentiment: {sentiment} ({score:+d})")
            st.markdown(f"[Read source]({item.url})")
    if not relevant:
        st.info("No asset-specific headlines found in the latest feed refresh.")
except NewsError as exc:
    st.warning(f"News refresh failed: {exc}. No cached headline is presented as fresh.")
st.caption("News classification is keyword-based, can be wrong, and never overrides price/risk checks. Signals are not an LLM's prediction.")
