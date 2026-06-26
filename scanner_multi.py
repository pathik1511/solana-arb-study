#!/usr/bin/env python3
r"""
STAGE B — Multi-Token Cross-DEX Arbitrage Scanner (live, read-only)
==================================================================

Generalizes Stage 1 from a single pair to a configurable BASKET of tokens, ranks
them by real round-trip edge, and logs every scan to CSV for later analysis.

The point: see *across many tokens* where price gaps actually live. Liquid majors
(SOL) are arbitraged to ~0%. Gaps only show up on thinner/more volatile tokens —
and many tokens don't even have direct liquidity on 2+ DEXs to arbitrage between,
which the scanner reports honestly instead of pretending.

READ-ONLY. No wallet, no trading. Standard library only (urllib + csv).

Usage:
    python scanner_multi.py --usd 20 --rounds 5
    python scanner_multi.py --once
    python scanner_multi.py --tokens SOL,BONK        # subset of the basket
CSV is appended to logs/opportunities.csv.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://lite-api.jup.ag/swap/v1/quote"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDC_DECIMALS = 6

# DEXs probed per token (direct routes only, so each price = one real pool).
DEXES = ["Raydium", "Orca", "Meteora", "Lifinity V2", "Phoenix"]

# Configurable basket: symbol -> (mint, decimals). Add your own freely.
TOKENS = {
    "SOL":  ("So11111111111111111111111111111111111111112", 9),
    "BONK": ("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", 5),
    "JUP":  ("JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN", 6),
    "WIF":  ("EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", 6),
}

EST_PRIORITY_FEE_USD = 0.40
REQUEST_SPACING_S = 0.2
LOGFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "opportunities.csv")


class RateLimited(Exception):
    pass


def quote(in_mint, out_mint, amount, dex):
    params = {"inputMint": in_mint, "outputMint": out_mint, "amount": str(amount),
              "slippageBps": "50", "dexes": dex, "onlyDirectRoutes": "true"}
    url = API + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=15) as r:
            d = json.loads(r.read())
        return int(d["outAmount"]) if "outAmount" in d else None
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimited()
        return None
    except Exception:
        return None


def scan_token(symbol: str, usd: float) -> dict:
    """Probe one token across DEXs; return its best cross-DEX round-trip edge."""
    mint, _dec = TOKENS[symbol]
    usdc_base = int(usd * 10**USDC_DECIMALS)

    # 1. Per-DEX price: token received for a fixed USDC probe (more = cheaper token).
    tok_out = {}
    for dex in DEXES:
        out = quote(USDC, mint, usdc_base, dex)
        if out and out > 0:
            tok_out[dex] = out
        time.sleep(REQUEST_SPACING_S)

    if len(tok_out) < 2:
        return {"symbol": symbol, "n_dex": len(tok_out), "comparable": False}

    # cheapest token = most tokens out per USDC = best place to BUY
    cheap = max(tok_out, key=tok_out.get)
    dear = min(tok_out, key=tok_out.get)
    gap_pct = (tok_out[cheap] / tok_out[dear] - 1) * 100

    # 2. Real round trip: buy on cheap DEX, sell those tokens on dear DEX.
    usdc_back = quote(TOKENS[symbol][0], USDC, tok_out[cheap], dear)
    time.sleep(REQUEST_SPACING_S)
    net = None
    if usdc_back:
        net = (usdc_back - usdc_base) / 10**USDC_DECIMALS - EST_PRIORITY_FEE_USD
    return {"symbol": symbol, "n_dex": len(tok_out), "comparable": True,
            "buy_dex": cheap, "sell_dex": dear, "gap_pct": gap_pct, "net": net}


def log_rows(rows: list[dict]):
    os.makedirs(os.path.dirname(LOGFILE), exist_ok=True)
    new = not os.path.exists(LOGFILE)
    with open(LOGFILE, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "symbol", "n_dex", "buy_dex", "sell_dex", "gap_pct", "net_usd"])
        for r in rows:
            if r.get("comparable"):
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), r["symbol"], r["n_dex"],
                            r["buy_dex"], r["sell_dex"], f"{r['gap_pct']:.4f}",
                            f"{r['net']:.4f}" if r["net"] is not None else ""])


def run_round(usd: float, symbols: list[str]):
    results = []
    for sym in symbols:
        try:
            results.append(scan_token(sym, usd))
        except RateLimited:
            print(f"  {sym}: rate-limited, backing off 15s...")
            time.sleep(15)
    # rank comparable tokens by gap (desc)
    comp = sorted([r for r in results if r.get("comparable")],
                  key=lambda r: r["gap_pct"], reverse=True)
    thin = [r for r in results if not r.get("comparable")]
    print(f"[{time.strftime('%H:%M:%S')}] ranked by cross-DEX gap:")
    for r in comp:
        net = f"${r['net']:+.4f}" if r["net"] is not None else "n/a"
        flag = "PROFIT ✅" if (r["net"] or -1) > 0 else "no edge"
        print(f"   {r['symbol']:5s} gap {r['gap_pct']:6.3f}%  buy {r['buy_dex']:>10s} "
              f"sell {r['sell_dex']:>10s}  round-trip {net}  {flag}")
    for r in thin:
        print(f"   {r['symbol']:5s} thin — only {r['n_dex']} DEX with direct liquidity (can't arb)")
    log_rows(results)
    return results


def main():
    p = argparse.ArgumentParser(description="Stage B: multi-token cross-DEX scanner")
    p.add_argument("--usd", type=float, default=20.0)
    p.add_argument("--rounds", type=int, default=5)
    p.add_argument("--interval", type=float, default=6.0)
    p.add_argument("--once", action="store_true")
    p.add_argument("--tokens", type=str, default="", help="comma list, e.g. SOL,BONK")
    args = p.parse_args()

    symbols = [s.strip().upper() for s in args.tokens.split(",") if s.strip()] or list(TOKENS)
    bad = [s for s in symbols if s not in TOKENS]
    if bad:
        raise SystemExit(f"Unknown tokens: {bad}. Known: {list(TOKENS)}")

    print("Stage B — Multi-Token Cross-DEX Scanner (read-only)")
    print(f"Basket: {', '.join(symbols)} | probe ${args.usd:.2f} | logging to logs/opportunities.csv\n")

    rounds = 1 if args.once else args.rounds
    for i in range(rounds):
        run_round(args.usd, symbols)
        if not args.once and i < rounds - 1:
            time.sleep(args.interval)
    print(f"\nLogged to {os.path.relpath(LOGFILE)}. Open it in Excel to chart gaps over time.")


if __name__ == "__main__":
    main()
