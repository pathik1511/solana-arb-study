"""
Modern Streamlit control panel for the Solana arbitrage project.

Launch (easiest):  double-click run_dashboard.bat
Or manually:       .\.venv\Scripts\python.exe -m streamlit run dashboard.py

Tabs: Live Scanner · 3D Map · Run Tools.
Read-only market data. The only thing that touches a network with keys is the
devnet latency test, which runs on Solana DEVNET with test money — never mainnet.
"""

import os
import subprocess
import sys
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import scanner_multi as sm
import store

HERE = os.path.dirname(os.path.abspath(__file__))
ACCENT = "#00e0c6"
GREEN = "#2ecc71"
RED = "#ff6b6b"
AMBER = "#ffcc4d"

st.set_page_config(page_title="Solana Arb Terminal", page_icon="📊", layout="wide")

# --------------------------------------------------------------------------- #
# Design system — modern glass UI, font, animations
# --------------------------------------------------------------------------- #
st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
      html, body, [class*="css"], .stMarkdown, button, input { font-family:'Inter',sans-serif !important; }

      @keyframes pulse {0%{opacity:1}50%{opacity:.25}100%{opacity:1}}
      @keyframes fadeInUp {from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:translateY(0)}}
      @keyframes shimmer {0%{background-position:-200% center}100%{background-position:200% center}}
      @keyframes floatGlow {0%,100%{box-shadow:0 0 24px rgba(0,224,198,.10)}50%{box-shadow:0 0 36px rgba(0,224,198,.22)}}

      .stApp { background:
        radial-gradient(1200px 600px at 12% -10%, rgba(0,224,198,.08), transparent 60%),
        radial-gradient(900px 500px at 110% 10%, rgba(123,97,255,.10), transparent 55%), #0d1117; }

      .stTabs [data-baseweb="tab-panel"]{animation:fadeInUp .5s ease both}
      div[data-testid="stPlotlyChart"], div[data-testid="stDataFrame"]{animation:fadeInUp .55s ease both}
      .stTabs [data-baseweb="tab"]{font-weight:600;transition:all .2s ease}
      .stButton button{border-radius:12px;font-weight:600;transition:transform .18s ease, box-shadow .18s ease}
      .stButton button:hover{transform:translateY(-2px);box-shadow:0 6px 18px rgba(0,224,198,.28)}

      .live-dot{height:11px;width:11px;background:#2ecc71;border-radius:50%;display:inline-block;
                margin-right:8px;animation:pulse 1.3s infinite;box-shadow:0 0 8px #2ecc71}
      .live-badge{font-weight:700;letter-spacing:2px;color:#2ecc71}
      .subtle{color:#8b949e}
      .shimmer{background:linear-gradient(90deg,#00e0c6,#9af2e8,#7b61ff,#00e0c6);background-size:300% auto;
               -webkit-background-clip:text;background-clip:text;color:transparent;animation:shimmer 5s linear infinite}

      .kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin:6px 0 10px}
      .kpi-card{padding:16px 18px;border-radius:18px;border:1px solid rgba(255,255,255,.08);
                background:linear-gradient(160deg,rgba(0,224,198,.07),rgba(22,27,34,.55));
                backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
                box-shadow:0 8px 30px rgba(0,0,0,.35);animation:fadeInUp .5s ease both;transition:all .25s ease}
      .kpi-card:hover{transform:translateY(-4px);border-color:rgba(0,224,198,.45);
                box-shadow:0 14px 38px rgba(0,224,198,.15)}
      .kpi-label{color:#8b949e;font-size:.72rem;letter-spacing:.8px;text-transform:uppercase;font-weight:600}
      .kpi-value{font-size:1.8rem;font-weight:800;line-height:1.1;margin-top:6px}
      .kpi-delta{font-size:.82rem;margin-top:4px;font-weight:700}
      .pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:.72rem;font-weight:700;
            border:1px solid rgba(255,255,255,.12)}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    "<h1 style='margin-bottom:0;font-weight:800'>📊 Solana DEX Arbitrage "
    "<span class='shimmer'>Terminal</span></h1>"
    "<p class='subtle' style='margin-top:2px'>Real-time cross-DEX intelligence · "
    "read-only · no mainnet trading</p>",
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("⚙️ Controls")
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

    st.divider()
    hide_thin = st.toggle("Hide thin tokens", value=False,
                          help="Show only tokens with ≥2 DEXs (arbitrageable).")
    min_gap = st.slider("Min gap % to show", 0.0, 2.0, 0.0, 0.05)

    st.divider()
    scan_clicked = st.button("🔍 Scan now", type="primary", use_container_width=True)
    auto = st.checkbox("Auto-refresh")
    interval = st.slider("Refresh every (sec)", 5, 60, 15) if auto else 0

st.session_state.setdefault("history", [])
st.session_state.setdefault("prev_best", None)


# --------------------------------------------------------------------------- #
# Data helpers
# --------------------------------------------------------------------------- #
def run_scan(usd_amount, symbols, tmap):
    rows = []
    for sym in symbols:
        try:
            rows.append(sm.scan_token(sym, usd_amount, tmap))
        except sm.RateLimited:
            rows.append({"symbol": sym, "comparable": False, "n_dex": 0, "rate_limited": True})
    return rows


def to_table(results):
    data = []
    for r in results:
        if r.get("comparable"):
            net = r["net"]
            data.append({"Token": r["symbol"], "Gap %": round(r["gap_pct"], 3),
                         "Buy on": r["buy_dex"], "Sell on": r["sell_dex"],
                         "Round-trip $": round(net, 4) if net is not None else None,
                         "Edge": "✅ profit" if (net or -1) > 0 else "no edge"})
        else:
            label = "rate-limited" if r.get("rate_limited") else f"thin ({r.get('n_dex', 0)} DEX)"
            data.append({"Token": r["symbol"], "Gap %": None, "Buy on": "—",
                         "Sell on": "—", "Round-trip $": None, "Edge": label})
    return pd.DataFrame(data)


def sparkline_svg(values, w=150, h=34, color=ACCENT):
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    rng = (hi - lo) or 1.0
    pts = " ".join(
        f"{i/(len(values)-1)*w:.1f},{h - (v-lo)/rng*(h-6) - 3:.1f}"
        for i, v in enumerate(values))
    return (f"<svg viewBox='0 0 {w} {h}' preserveAspectRatio='none' "
            f"style='width:100%;height:34px;margin-top:8px'>"
            f"<polyline points='{pts}' fill='none' stroke='{color}' "
            f"stroke-width='2' stroke-linecap='round'/></svg>")


def kpi_card(label, value, delta_html="", spark=""):
    return (f"<div class='kpi-card'><div class='kpi-label'>{label}</div>"
            f"<div class='kpi-value'>{value}</div>{delta_html}{spark}</div>")


def gauge_fig(best):
    top = max(5.0, best * 1.25)
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=best, number={"suffix": "%", "font": {"size": 26}},
        gauge={"axis": {"range": [0, top], "tickcolor": "#8b949e"},
               "bar": {"color": ACCENT, "thickness": 0.28},
               "bgcolor": "rgba(0,0,0,0)", "borderwidth": 0,
               "steps": [{"range": [0, 1], "color": "rgba(46,204,113,.16)"},
                         {"range": [1, 3], "color": "rgba(255,204,77,.14)"},
                         {"range": [3, top], "color": "rgba(255,107,107,.14)"}]}))
    fig.update_layout(template="plotly_dark", height=200,
                      margin=dict(l=24, r=24, t=10, b=0), paper_bgcolor="rgba(0,0,0,0)")
    return fig


def bar_fig(df):
    d = df.dropna(subset=["Gap %"]).sort_values("Gap %")
    fig = go.Figure(go.Bar(x=d["Gap %"], y=d["Token"], orientation="h",
                           marker=dict(color=d["Gap %"], colorscale="Teal", showscale=False),
                           hovertemplate="%{y}: %{x:.3f}%<extra></extra>"))
    fig.update_layout(template="plotly_dark", height=max(220, 36 * len(d)),
                      margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", xaxis_title="cross-DEX gap %")
    return fig


def line_fig(hist):
    fig = go.Figure(go.Scatter(x=hist["time"], y=hist["best_gap"], mode="lines+markers",
                               line=dict(color=ACCENT, width=2.5, shape="spline"),
                               fill="tozeroy", fillcolor="rgba(0,224,198,0.12)",
                               hovertemplate="%{x}: %{y:.3f}%<extra></extra>"))
    fig.update_layout(template="plotly_dark", height=240, margin=dict(l=10, r=10, t=10, b=10),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      yaxis_title="best gap %")
    return fig


def scatter3d_fig(results):
    xs, ys, zs, colors, labels, names = [], [], [], [], [], []
    for r in results:
        n = r.get("n_dex", 0)
        if r.get("comparable"):
            gap = r["gap_pct"]
            net = r["net"] if r["net"] is not None else 0.0
            labels.append(f"{r['symbol']}<br>{n} DEX · gap {gap:.3f}% · net ${net:.3f}"
                          f"<br>{r['buy_dex']} → {r['sell_dex']}")
        else:
            gap, net = 0.0, 0.0
            labels.append(f"{r['symbol']}<br>thin ({n} DEX) — can't arbitrage")
        xs.append(n); ys.append(gap); zs.append(net); colors.append(net); names.append(r["symbol"])
    show_text = len(results) <= 30
    fig = go.Figure(go.Scatter3d(
        x=xs, y=ys, z=zs, mode="markers+text" if show_text else "markers",
        text=names if show_text else None, textposition="top center",
        textfont=dict(size=9, color="#8b949e"),
        marker=dict(size=6, color=colors, colorscale="RdYlGn",
                    cmin=min(colors + [-0.5]), cmax=max(colors + [0.5]),
                    showscale=True, colorbar=dict(title="net $"), opacity=0.9),
        hovertext=labels, hoverinfo="text"))
    fig.update_layout(template="plotly_dark", height=620, paper_bgcolor="rgba(0,0,0,0)",
                      margin=dict(l=0, r=0, t=0, b=0),
                      scene=dict(xaxis_title="# DEXs w/ liquidity", yaxis_title="gap %",
                                 zaxis_title="round-trip $"))
    return fig


def table_config(df):
    maxv = df["Gap %"].max()
    maxv = float(maxv) if pd.notna(maxv) and maxv > 0 else 1.0
    return {"Gap %": st.column_config.ProgressColumn("Gap %", format="%.3f",
                                                     min_value=0.0, max_value=maxv),
            "Round-trip $": st.column_config.NumberColumn("Round-trip $", format="$%.4f")}


def run_script(args, timeout, label):
    with st.spinner(label):
        try:
            r = subprocess.run([sys.executable, *args], capture_output=True,
                               text=True, timeout=timeout, cwd=HERE)
            return (r.stdout or "") + (r.stderr or "") or "(no output)"
        except subprocess.TimeoutExpired as e:
            partial = (e.stdout or "") if isinstance(e.stdout, str) else ""
            return partial + f"\n[stopped after {timeout}s timeout]"


scanner_tab, map_tab, history_tab, tools_tab = st.tabs(
    ["📡 Live Scanner", "🌐 3D Map", "📜 History", "🛠️ Run Tools"])

# --------------------------------------------------------------------------- #
# Tab 1 — Live Scanner
# --------------------------------------------------------------------------- #
with scanner_tab:
    if (scan_clicked or auto) and tokens:
        t0 = time.time()
        with st.spinner(f"Reading live Solana prices for {len(tokens)} tokens…"):
            results = run_scan(usd, tokens, token_map)
        scan_ms = (time.time() - t0) * 1000

        comp = [r for r in results if r.get("comparable")]
        best = max((r["gap_pct"] for r in comp), default=0.0)
        profitable = sum(1 for r in comp if (r["net"] or -1) > 0)
        prev = st.session_state.prev_best
        st.session_state.history.append({"time": time.strftime("%H:%M:%S"), "best_gap": best})
        hist = pd.DataFrame(st.session_state.history[-60:])

        st.markdown(
            f"<span class='live-dot'></span><span class='live-badge'>LIVE</span>"
            f"<span class='subtle'> &nbsp; last scan {time.strftime('%H:%M:%S')} · "
            f"{scan_ms:.0f} ms · {len(tokens)} tokens</span>", unsafe_allow_html=True)

        if prev is None:
            d_best = "<div class='kpi-delta' style='color:#8b949e'>—</div>"
        else:
            up = best >= prev
            d_best = (f"<div class='kpi-delta' style='color:{GREEN if up else RED}'>"
                      f"{'▲' if up else '▼'} {abs(best - prev):.3f}%</div>")
        cards = (
            kpi_card("Best gap", f"{best:.3f}%", d_best, sparkline_svg(list(hist['best_gap']))) +
            kpi_card("Profitable now", str(profitable),
                     f"<div class='kpi-delta' style='color:{GREEN if profitable else '#8b949e'}'>"
                     f"{'live edge' if profitable else 'none'}</div>") +
            kpi_card("Comparable", f"{len(comp)} / {len(results)}",
                     "<div class='kpi-delta subtle'>≥2 DEX liquidity</div>") +
            kpi_card("Scan latency", f"{scan_ms:.0f} ms",
                     "<div class='kpi-delta subtle'>round-trip quotes</div>")
        )
        st.markdown(f"<div class='kpi-grid'>{cards}</div>", unsafe_allow_html=True)

        # filters
        df = to_table(results)
        if hide_thin:
            df = df[df["Gap %"].notna()]
        if min_gap > 0:
            df = df[(df["Gap %"].isna()) | (df["Gap %"] >= min_gap)]
        df = df.sort_values("Gap %", ascending=False, na_position="last")

        g1, g2 = st.columns([1, 1.3])
        with g1:
            st.markdown("##### 🎯 Market pulse")
            st.plotly_chart(gauge_fig(best), use_container_width=True)
        with g2:
            st.markdown("##### 📈 Best gap over time")
            st.plotly_chart(line_fig(hist), use_container_width=True)

        b1, b2 = st.columns([1.1, 1])
        with b1:
            st.markdown("##### 📊 Gap by token")
            chart_df = df.dropna(subset=["Gap %"])
            if not chart_df.empty:
                st.plotly_chart(bar_fig(df), use_container_width=True)
            else:
                st.info("No token had ≥2 DEXs with direct liquidity this scan.")
        with b2:
            st.markdown("##### 📋 Order book")
            st.dataframe(df, use_container_width=True, hide_index=True,
                         column_config=table_config(df), height=320)
            st.download_button("⬇ Download scan (CSV)", df.to_csv(index=False).encode(),
                               "scan.csv", "text/csv", use_container_width=True)

        st.session_state.prev_best = best
        st.session_state.last_results = results
        store.record(results)
        st.caption("A visible gap rarely survives the real round-trip after fees and "
                   "slippage. Profitable rows are rare and fleeting — a study tool, "
                   "not a trading signal.")
    elif not tokens:
        st.warning("Pick at least one token in the sidebar.")
    else:
        st.info("Set your options in the sidebar and click **Scan now** to begin.")

# --------------------------------------------------------------------------- #
# Tab 2 — 3D Map
# --------------------------------------------------------------------------- #
with map_tab:
    st.subheader("3D token map — liquidity × gap × profitability")
    res = st.session_state.get("last_results")
    if not res:
        st.info("Run a scan in the **Live Scanner** tab first, then come back here.")
    else:
        st.plotly_chart(scatter3d_fig(res), use_container_width=True)
        st.caption("Each point is a token. **Drag to rotate, scroll to zoom, hover for "
                   "details.** Most tokens sit at low liquidity / zero edge; the few that "
                   "float up have tiny gaps and (almost always) negative net. "
                   "Tip: use *Top N by volume* for a denser cloud.")

# --------------------------------------------------------------------------- #
# Tab 3 — History & backtest (persistent, from SQLite)
# --------------------------------------------------------------------------- #
with history_tab:
    st.subheader("📜 Persistent history & backtest")
    s = store.stats()
    if s["total_rows"] == 0:
        st.info("No history yet. Run scans in the **Live Scanner** tab — every scan "
                "is saved to `scans.db` and survives restarts.")
    else:
        h = pd.DataFrame(store.load(5000),
                         columns=["ts", "symbol", "n_dex", "comparable",
                                  "buy_dex", "sell_dex", "gap_pct", "net"])
        h["ts"] = pd.to_datetime(h["ts"])

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Rows recorded", f"{s['total_rows']:,}")
        c2.metric("Comparable signals", f"{s['comparable']:,}")
        c3.metric("Ever profitable", f"{s['profitable']:,}")
        c4.metric("P&L if you traded all", f"${s['pnl_if_traded_all']:.2f}",
                  delta=f"winners-only ${s['pnl_if_winners_only']:.2f}")
        st.caption(f"Spans {s['first']} → {s['last']}")

        gtime = (h.dropna(subset=["gap_pct"])
                  .assign(t=h["ts"].dt.strftime("%m-%d %H:%M:%S"))
                  .groupby("t")["gap_pct"].max())
        st.markdown("##### Best gap over time (persisted)")
        st.line_chart(gtime, height=240)

        comp = h[h["comparable"] == 1].dropna(subset=["net"]).sort_values("ts")
        if not comp.empty:
            cum = comp["net"].cumsum()
            cum.index = comp["ts"].dt.strftime("%m-%d %H:%M:%S")
            st.markdown("##### Backtest: cumulative P&L if you traded every signal")
            st.area_chart(cum, height=240, color="#ff6b6b")

        wins = h[(h["net"].notna()) & (h["net"] > 0)]
        if wins.empty:
            st.success("Backtest verdict: **not a single recorded gap survived the "
                       "round-trip as a profit.** This is the honest, accumulated result.")
        else:
            st.markdown("##### Profitable signals recorded")
            st.dataframe(wins[["ts", "symbol", "gap_pct", "buy_dex", "sell_dex", "net"]],
                         use_container_width=True, hide_index=True)

        d1, d2 = st.columns(2)
        d1.download_button("⬇ Download history (CSV)", h.to_csv(index=False).encode(),
                           "history.csv", "text/csv", use_container_width=True)
        if d2.button("🗑 Clear history", use_container_width=True):
            store.clear()
            st.rerun()

# --------------------------------------------------------------------------- #
# Tab 4 — Run Tools
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
    st.markdown("**Create / fund the devnet wallet** — needed once before the latency "
                "test. May wait on the faucet; if it times out, fund the printed "
                "address at faucet.solana.com and re-run.")
    if st.button("▶ Run stage2_devnet_bot.py --fund"):
        st.code(run_script(["stage2_devnet_bot.py", "--fund"], timeout=120,
                           label="Creating/funding devnet key…"))

    st.caption("Tools run with this app's Python. The devnet test never touches "
               "mainnet or real funds.")

if auto and tokens:
    time.sleep(interval)
    st.rerun()
