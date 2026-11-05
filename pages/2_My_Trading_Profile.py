"""Personal trading profile and paper-journal review."""
from __future__ import annotations

import json
import pandas as pd
import streamlit as st

from paperbot.personal_profile import DEFAULT_PROFILE, normalise_profile

st.set_page_config(page_title="My Trading Profile", page_icon="🎯", layout="wide")
st.title("🎯 My Trading Profile")
st.caption("Personal preferences for your BTC, ETH, SOL and XRP paper-trading research.")

if "personal_trading_profile" not in st.session_state:
    st.session_state.personal_trading_profile = dict(DEFAULT_PROFILE)
profile = normalise_profile(st.session_state.personal_trading_profile)

st.info(
    "Balanced is your starting profile: 0.5% reference risk per trade, "
    "2% daily loss limit, and at least 1.5R planned reward/risk. "
    "These are research guardrails, not guarantees."
)

with st.form("personal_trading_profile_form"):
    left, right = st.columns(2)
    with left:
        risk_mode = st.selectbox("Risk style", ["Conservative", "Balanced", "Aggressive"],
                                 index=["Conservative", "Balanced", "Aggressive"].index(profile["risk_mode"]))
        preferred_symbol = st.selectbox("Preferred market", ["SOLUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"],
                                        index=["SOLUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"].index(profile["preferred_symbol"]))
        trading_style = st.radio("Main trading style", ["scalping", "intraday"],
                                 index=["scalping", "intraday"].index(profile["trading_style"]))
        reference_capital = st.number_input("Paper reference capital (USDT)", min_value=10.0,
                                             max_value=100000.0, value=float(profile["reference_capital_usdt"]), step=10.0)
    with right:
        risk_pct = st.slider("Risk per trade (%)", 0.1, 1.0, float(profile["risk_per_trade_pct"]), 0.1)
        daily_loss_pct = st.slider("Maximum daily loss (%)", 0.5, 5.0, float(profile["max_daily_loss_pct"]), 0.5)
        min_rr = st.slider("Minimum planned reward/risk (R)", 1.0, 4.0, float(profile["minimum_risk_reward"]), 0.25)
        min_score = st.slider("Minimum setup score", 3.0, 6.0, float(profile["minimum_setup_score"]), 0.5)
    saved = st.form_submit_button("Save my profile", type="primary", use_container_width=True)

if saved:
    st.session_state.personal_trading_profile = normalise_profile({
        "risk_mode": risk_mode, "preferred_symbol": preferred_symbol,
        "trading_style": trading_style, "reference_capital_usdt": reference_capital,
        "risk_per_trade_pct": risk_pct, "max_daily_loss_pct": daily_loss_pct,
        "minimum_risk_reward": min_rr, "minimum_setup_score": min_score,
    })
    st.success("Profile applied to this Streamlit session. Open AI Market Analyst to use it.")

profile = normalise_profile(st.session_state.personal_trading_profile)
st.subheader("Your risk calculator")
entry = st.number_input("Planned entry price", min_value=0.0, value=0.0, format="%.8f")
stop = st.number_input("Planned stop-loss price", min_value=0.0, value=0.0, format="%.8f")
if entry > 0 and stop > 0 and entry != stop:
    risk_amount = profile["reference_capital_usdt"] * profile["risk_per_trade_pct"] / 100
    qty = risk_amount / abs(entry - stop)
    c1, c2, c3 = st.columns(3)
    c1.metric("Maximum planned loss", f"{risk_amount:.2f} USDT")
    c2.metric("Estimated base quantity", f"{qty:.8f}")
    c3.metric("Estimated notional", f"{qty * entry:.2f} USDT")
    st.caption("Approximation only. It excludes contract multipliers, lot sizes, fees, slippage and funding. Never copy it as an exchange order quantity.")

st.divider()
st.subheader("Review your paper-trading journal")
journal = st.file_uploader("Import a paper-trade CSV (optional)", type=["csv"])
if journal is not None:
    try:
        trades = pd.read_csv(journal)
        st.write(f"Imported {len(trades)} rows for this review only.")
        numeric_cols = [c for c in trades.columns if any(k in c.lower() for k in ("pnl", "profit", "net"))]
        pnl_col = next((c for c in numeric_cols if "net" in c.lower()), numeric_cols[0] if numeric_cols else None)
        if pnl_col:
            pnl = pd.to_numeric(trades[pnl_col], errors="coerce").dropna()
            if len(pnl):
                a, b, c = st.columns(3)
                a.metric("Net P&L", f"{pnl.sum():+.2f}")
                b.metric("Win rate", f"{(pnl > 0).mean() * 100:.1f}%")
                b.caption(f"Based on {len(pnl)} valid P&L rows")
                c.metric("Average trade", f"{pnl.mean():+.4f}")
                st.subheader("Recent imported trades")
                st.dataframe(trades.tail(30), use_container_width=True, hide_index=True)
                losing = int((pnl < 0).sum())
                if losing:
                    st.warning(f"{losing} losing trades in this import. Review their setup, time, fees and execution before repeating them.")
                else:
                    st.success("No losing rows in this imported sample.")
        else:
            st.warning("Could not identify a P&L column. Keep the original CSV headers and check that it includes net P&L.")
    except Exception as exc:
        st.error(f"Could not read this CSV: {type(exc).__name__}: {exc}")
else:
    st.caption("Import a CSV exported from your paper journal to review wins, losses and average P&L. Nothing is uploaded to the worker account.")

st.divider()
st.subheader("Save or restore your profile")
profile_json = json.dumps(profile, indent=2)
st.download_button("Download my profile (JSON)", profile_json, file_name="my_paper_trading_profile.json",
                   mime="application/json")
restore = st.file_uploader("Restore a previously saved profile (JSON)", type=["json"], key="profile_restore")
if restore is not None and st.button("Restore profile"):
    try:
        st.session_state.personal_trading_profile = normalise_profile(json.load(restore))
        st.success("Profile restored for this session. Refresh this page if the controls do not update.")
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        st.error(f"Invalid profile file: {exc}")

st.caption("Profile settings are session-scoped on this separate dashboard. Download the JSON to keep a backup; this page does not write to or modify the worker's persistent paper-trading database.")
