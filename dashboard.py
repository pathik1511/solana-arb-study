"""
Interactive Streamlit dashboard + control panel for the Solana arbitrage project.

Launch (easiest):  double-click run_dashboard.bat
Or manually:       .\.venv\Scripts\python.exe -m streamlit run dashboard.py

Two tabs:
  • Live Scanner — read live cross-DEX gaps, ranked, with charts.
  • Run Tools    — buttons that run each project script (no terminal needed).

Read-only market data. The only thing that touches a network with keys is the
devnet latency test, which runs on Solana DEVNET with test money — never mainnet.
"""

import os
import subprocess
import sys
import time

import pandas as pd
import streamlit as st

import scanner_multi as sm

HERE = os.path.dirname(os.path.abspath(__file__))

st.set_page_config(page_title="Solana Arb Scanner", page_icon="📊", layout="wide")
st.title("📊 Solana DEX Arbitrage — Control Panel")
st.caption("Read-only market data. No mainnet trading anywhere in this app.")

# --------------------------------------------------------------------------- #
# Sidebar — scanner settings
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("Scanner settings")
    usd = st.slider("Probe size (USD)", 5, 100, 20, 5)

    source = st.radio("Token universe", ["Curated basket", "Top N by volume"])
    if source == "Top N by volume":
        n = st.slider("How many top tokens", 5, 100, 20, 5)
        if n > 30:
            st.warning("Large N is slow and rate-limited on the free API.")
        token_map = sm.fetch_top_tokens(n)
        tokens = list(token_map)
    else:
        token_map = dict(sm.TOKENS)
        tokens = st.multiselect("Tokens", list(sm.TOKENS), default=list(sm.TOKENS))

    scan_clicked = st.button("🔍 Scan now", type="primary", use_container_width=True)
    auto = st.checkbox("Auto-refresh")
    interval = st.slider("Refresh every (sec)", 5, 60, 15) if auto else 0

if "history" not in st.session_state:
    st.session_state.history = []


def run_scan(usd_amount: float, symbols: list[str], tmap: dict) -> list[dict]:
    rows = []
    for sym in symbols:
        try:
            rows.append(sm.scan_token(sym, usd_amount, tmap))
        except sm.RateLimited:
            rows.append({"symbol": sym, "comparable": False, "n_dex": 0, "rate_limited": True})
    return rows


def to_table(results: list[dict]) -> pd.DataFrame:
    data = []
    for r in results:
        if r.get("comparable"):
            net = r["net"]
            data.append({
                "Token": r["symbol"],
                "Gap %": round(r["gap_pct"], 3),
                "Buy on": r["buy_dex"],
                "Sell on": r["sell_dex"],
                "Round-trip $": round(net, 4) if net is not None else None,
                "Edge": "✅ profit" if (net or -1) > 0 else "no edge",
            })
        else:
            label = "rate-limited" if r.get("rate_limited") else f"thin ({r.get('n_dex', 0)} DEX)"
            data.append({"Token": r["symbol"], "Gap %": None, "Buy on": "—",
                         "Sell on": "—", "Round-trip $": None, "Edge": label})
    return pd.DataFrame(data)


def run_script(args: list[str], timeout: int, label: str) -> str:
    """Run a project script with the SAME Python and capture its output."""
    with st.spinner(label):
        try:
            r = subprocess.run([sys.executable, *args], capture_output=True,
                               text=True, timeout=timeout, cwd=HERE)
            return (r.stdout or "") + (r.stderr or "") or "(no output)"
        except subprocess.TimeoutExpired as e:
            partial = (e.stdout or "") if isinstance(e.stdout, str) else ""
            return partial + f"\n[stopped after {timeout}s timeout]"


scanner_tab, tools_tab = st.tabs(["📡 Live Scanner", "🛠️ Run Tools"])

# --------------------------------------------------------------------------- #
# Tab 1 — Live Scanner
# --------------------------------------------------------------------------- #
with scanner_tab:
    if (scan_clicked or auto) and tokens:
        with st.spinner(f"Reading live Solana prices for {len(tokens)} tokens…"):
            results = run_scan(usd, tokens, token_map)

        comp = [r for r in results if r.get("comparable")]
        best = max((r["gap_pct"] for r in comp), default=0.0)
        profitable = sum(1 for r in comp if (r["net"] or -1) > 0)

        c1, c2, c3 = st.columns(3)
        c1.metric("Tokens scanned", len(results))
        c2.metric("Best gap", f"{best:.3f}%")
        c3.metric("Profitable now", profitable)

        df = to_table(results).sort_values("Gap %", ascending=False, na_position="last")
        st.subheader("Latest scan")
        st.dataframe(df, use_container_width=True, hide_index=True)

        chart = df.dropna(subset=["Gap %"]).set_index("Token")["Gap %"]
        if not chart.empty:
            st.subheader("Cross-DEX gap by token (%)")
            st.bar_chart(chart)

        st.session_state.history.append({"time": time.strftime("%H:%M:%S"), "best_gap %": best})
        hist = pd.DataFrame(st.session_state.history[-60:]).set_index("time")
        st.subheader("Best gap over time")
        st.line_chart(hist)

        st.info("A visible gap rarely survives the real round-trip after fees and "
                "slippage. Profitable rows are rare and fleeting — a study tool, "
                "not a trading signal.")
    elif not tokens:
        st.warning("Pick at least one token in the sidebar.")
    else:
        st.info("Set your options in the sidebar and click **Scan now** to begin.")

# --------------------------------------------------------------------------- #
# Tab 2 — Run Tools (buttons instead of the CLI)
# --------------------------------------------------------------------------- #
with tools_tab:
    st.subheader("Run any tool with a button — no terminal")

    st.markdown("**Offline arbitrage model** — built-in scenarios, no network.")
    if st.button("▶ Run arb_bot.py"):
        st.code(run_script(["arb_bot.py"], timeout=30, label="Running scenarios…"))

    st.divider()
    st.markdown("**Single-pair live scan** — one read-only SOL/USDC pass.")
    sp_usd = st.number_input("Probe size (USD)", 5, 100, 20, 5, key="sp_usd")
    if st.button("▶ Run live_scanner.py --once"):
        st.code(run_script(["live_scanner.py", "--usd", str(sp_usd), "--once"],
                           timeout=60, label="Scanning live prices…"))

    st.divider()
    st.markdown("**Devnet latency test** — real signed transactions on Solana "
                "**devnet** (test money). Requires a funded `devnet_key.json`.")
    cyc = st.number_input("Cycles", 1, 20, 5, key="cyc")
    detect = st.checkbox("Also show live mainnet gap each cycle", value=False)
    if st.button("▶ Run stage2_fast.py (devnet)"):
        args = ["stage2_fast.py", "--cycles", str(cyc)]
        if not detect:
            args.append("--no-detect")
        st.code(run_script(args, timeout=180, label="Running devnet cycles…"))

    st.divider()
    st.markdown("**Create / fund the devnet wallet** — needed once before the "
                "latency test. May wait on the faucet; if it times out, fund the "
                "printed address at faucet.solana.com and re-run.")
    if st.button("▶ Run stage2_devnet_bot.py --fund"):
        st.code(run_script(["stage2_devnet_bot.py", "--fund"], timeout=120,
                           label="Creating/funding devnet key…"))

    st.caption("Tools run with this app's Python. The devnet test never touches "
               "mainnet or real funds.")

# Auto-refresh loop (scanner only)
if auto and tokens:
    time.sleep(interval)
    st.rerun()
