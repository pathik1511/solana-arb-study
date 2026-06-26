#!/usr/bin/env python3
"""
STAGE 1 — Live Read-Only Solana Arbitrage Scanner
==================================================

Reads REAL Solana DEX prices via Jupiter's free public quote API and reports
real cross-DEX price gaps in real time. It then simulates a round trip
(buy on the cheap DEX, sell on the expensive one) to estimate whether the gap
would actually be profitable after a priority fee.

READ-ONLY. There is NO wallet, NO key, NO signing, NO trading anywhere in this
file. It only fetches public quotes. You cannot lose money running it.

Why this matters (Stage 1's whole point): watch how often a *profitable* gap
actually appears on liquid pairs, and how fast it vanishes. That observation is
what should inform whether Stages 2/3 are worth it for you.

Usage:
    python live_scanner.py                 # scan SOL priced in USDC, default size
    python live_scanner.py --usd 50        # use a ~$50 notional probe
    python live_scanner.py --interval 3    # seconds between scans
    python live_scanner.py --once          # single scan then exit

No API key required. Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
API = "https://lite-api.jup.ag/swap/v1/quote"

# Mints we trade between. Quote token = USDC (the numeraire), base = SOL.
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # 6 decimals
SOL = "So11111111111111111111111111111111111111112"    # 9 decimals
USDC_DECIMALS = 6
SOL_DECIMALS = 9

# DEXs to probe individually. Jupiter exposes many; these are the deep ones.
# Fewer DEXs = fewer API calls per scan = less chance of rate-limiting.
DEXES = ["Raydium", "Orca", "Meteora"]

# Polite spacing between individual API calls (the free endpoint is rate-limited).
REQUEST_SPACING_S = 0.2

# Rough cost of landing one atomic transaction, in USD. Real fee wars go higher;
# this is a conservative floor so the scanner doesn't cry "profit" on dust.
EST_PRIORITY_FEE_USD = 0.40


# --------------------------------------------------------------------------- #
# API helper
# --------------------------------------------------------------------------- #
def quote(input_mint: str, output_mint: str, amount: int, dex: str | None = None) -> dict | None:
    """ExactIn quote: how much output_mint you get for `amount` of input_mint.

    If `dex` is given, restrict to that single DEX with direct routes only, so
    the price reflects ONE pool (which is what an arbitrage leg actually hits).
    Returns parsed JSON dict, or None if no route / error.
    """
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount),
        "slippageBps": "50",
    }
    if dex:
        params["dexes"] = dex
        params["onlyDirectRoutes"] = "true"
    url = API + "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        if "outAmount" not in data:
            return None
        return data
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimited()  # surface so the caller can back off
        return None
    except Exception:
        return None


class RateLimited(Exception):
    """Raised when the free Jupiter endpoint asks us to slow down (HTTP 429)."""


def sol_out_for_usdc(usdc_base: int, dex: str) -> int | None:
    q = quote(USDC, SOL, usdc_base, dex)
    return int(q["outAmount"]) if q else None


def usdc_out_for_sol(sol_base: int, dex: str) -> int | None:
    q = quote(SOL, USDC, sol_base, dex)
    return int(q["outAmount"]) if q else None


# --------------------------------------------------------------------------- #
# One scan
# --------------------------------------------------------------------------- #
def scan_once(usdc_notional: float) -> None:
    usdc_base = int(usdc_notional * 10**USDC_DECIMALS)
    ts = time.strftime("%H:%M:%S")

    # 1. Per-DEX price: SOL received per the same USDC probe. More SOL = cheaper SOL.
    prices = {}  # dex -> SOL price in USDC (USDC per 1 SOL)
    sol_received = {}  # dex -> base SOL out
    for dex in DEXES:
        out = sol_out_for_usdc(usdc_base, dex)
        if out and out > 0:
            sol_units = out / 10**SOL_DECIMALS
            sol_received[dex] = out
            prices[dex] = usdc_notional / sol_units  # USDC per SOL
        time.sleep(REQUEST_SPACING_S)  # be polite to the free endpoint

    if len(prices) < 2:
        print(f"[{ts}] only {len(prices)} DEX(s) quoted — need 2+ to compare. Skipping.")
        return

    cheapest = min(prices, key=prices.get)   # lowest USDC/SOL = best place to BUY sol
    dearest = max(prices, key=prices.get)    # highest USDC/SOL = best place to SELL sol
    gap_pct = (prices[dearest] / prices[cheapest] - 1) * 100

    # 2. Round-trip simulation: spend USDC buying SOL on `cheapest`,
    #    then sell that SOL on `dearest`. Both legs are REAL quotes.
    sol_bought = sol_received[cheapest]
    usdc_back = usdc_out_for_sol(sol_bought, dearest)
    line = (f"[{ts}] gap {gap_pct:5.3f}%  buy {cheapest:>10s} @ {prices[cheapest]:.4f}  "
            f"sell {dearest:>10s} @ {prices[dearest]:.4f}")
    if usdc_back is None:
        print(line + "  (sell leg had no route)")
        return

    net = (usdc_back - usdc_base) / 10**USDC_DECIMALS - EST_PRIORITY_FEE_USD
    flag = "PROFITABLE ✅" if net > 0 else "no edge"
    print(line + f"  round-trip net ${net:+.4f}  {flag}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    p = argparse.ArgumentParser(description="Stage 1: live read-only Solana arbitrage scanner")
    p.add_argument("--usd", type=float, default=20.0, help="USDC notional to probe with")
    p.add_argument("--interval", type=float, default=4.0, help="seconds between scans")
    p.add_argument("--once", action="store_true", help="single scan then exit")
    args = p.parse_args()

    print("Stage 1 — Live Read-Only Scanner (no wallet, no trading)")
    print(f"Pair: SOL priced in USDC | probe ${args.usd:.2f} | DEXs: {', '.join(DEXES)}")
    print(f"Assuming ${EST_PRIORITY_FEE_USD:.2f} priority fee per round trip.\n")

    if args.once:
        try:
            scan_once(args.usd)
        except RateLimited:
            print("Rate-limited by the free API on the first call — wait a moment and retry.")
        return
    try:
        while True:
            try:
                scan_once(args.usd)
            except RateLimited:
                print(f"[{time.strftime('%H:%M:%S')}] rate-limited — backing off 15s...")
                time.sleep(15)
                continue
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
