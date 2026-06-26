#!/usr/bin/env python3
"""
Solana-style DEX Arbitrage Detector — PROTOTYPE (dry-run only)
==============================================================

This is an EDUCATIONAL prototype that models the exact mechanism behind the
viral "turned <$1 into a fortune" Solana trades: the same token quoted at two
different prices in two liquidity pools, closed by an atomic buy-low/sell-high.

It does NOT connect to a wallet, sign transactions, or trade. It:
  1. Models two constant-product (x*y=k) AMM pools for the same token pair.
  2. Detects a price gap between them.
  3. Solves for the trade size that maximises profit.
  4. Subtracts realistic costs (LP fees, priority/gas fee, optional flash-loan fee).
  5. Reports whether the opportunity clears a configurable profit threshold.

Run:
    python arb_bot.py                 # built-in scenarios (incl. an ANB-like gap)
    python arb_bot.py --watch         # loop with randomly drifting pools (sim)
    python arb_bot.py --help

Safe by design: there is no trading code path here. Wiring this to a real chain
is left as a deliberate, clearly-marked TODO so you opt in consciously.
"""

from __future__ import annotations

import argparse
import math
import random
import time
from dataclasses import dataclass


# --------------------------------------------------------------------------- #
# Pool model: constant-product AMM (Uniswap/Raydium/Meteora DAMM style)
# --------------------------------------------------------------------------- #
@dataclass
class Pool:
    """A constant-product pool holding `base` token and `quote` token.

    Price of base (in quote) = quote_reserve / base_reserve.
    Swap fee is the fraction the pool keeps (e.g. 0.0025 = 0.25%).
    """
    name: str
    base_reserve: float   # units of the token being arbitraged
    quote_reserve: float  # units of the numeraire (e.g. USDC or SOL)
    fee: float = 0.0025

    @property
    def price(self) -> float:
        return self.quote_reserve / self.base_reserve

    def buy_base(self, quote_in: float) -> float:
        """Spend `quote_in` of quote token, receive base token out."""
        quote_in_after_fee = quote_in * (1 - self.fee)
        k = self.base_reserve * self.quote_reserve
        new_quote = self.quote_reserve + quote_in_after_fee
        new_base = k / new_quote
        return self.base_reserve - new_base  # base received

    def sell_base(self, base_in: float) -> float:
        """Sell `base_in` of base token, receive quote token out."""
        base_in_after_fee = base_in * (1 - self.fee)
        k = self.base_reserve * self.quote_reserve
        new_base = self.base_reserve + base_in_after_fee
        new_quote = k / new_base
        return self.quote_reserve - new_quote  # quote received


# --------------------------------------------------------------------------- #
# Cost model
# --------------------------------------------------------------------------- #
@dataclass
class Costs:
    priority_fee_quote: float = 0.5   # priority/gas fee paid per atomic bundle, in quote units
    flash_loan_fee: float = 0.0       # fraction charged on borrowed notional (0 if self-funded)
    min_profit_quote: float = 1.0     # don't act below this net profit


# --------------------------------------------------------------------------- #
# Core: simulate a buy-low / sell-high round trip
# --------------------------------------------------------------------------- #
def round_trip_profit(cheap: Pool, expensive: Pool, quote_in: float, costs: Costs) -> float:
    """Net quote profit from spending `quote_in` to buy in `cheap`, sell in `expensive`."""
    base_bought = cheap.buy_base(quote_in)
    quote_back = expensive.sell_base(base_bought)
    gross = quote_back - quote_in
    fees = costs.priority_fee_quote + costs.flash_loan_fee * quote_in
    return gross - fees


def optimal_trade(cheap: Pool, expensive: Pool, costs: Costs) -> tuple[float, float]:
    """Find the quote_in that maximises net profit via a golden-section search.

    Returns (best_quote_in, best_net_profit). Searches up to a sane fraction of
    the cheap pool's quote depth — you can never profitably push more than that.
    """
    lo, hi = 0.0, cheap.quote_reserve * 0.95
    gr = (math.sqrt(5) - 1) / 2
    a, b = hi - gr * (hi - lo), lo + gr * (hi - lo)
    fa = round_trip_profit(cheap, expensive, a, costs)
    fb = round_trip_profit(cheap, expensive, b, costs)
    for _ in range(80):
        if fa < fb:
            lo, a, fa = a, b, fb
            b = lo + gr * (hi - lo)
            fb = round_trip_profit(cheap, expensive, b, costs)
        else:
            hi, b, fb = b, a, fa
            a = hi - gr * (hi - lo)
            fa = round_trip_profit(cheap, expensive, a, costs)
    best_in = (a + b) / 2
    return best_in, round_trip_profit(cheap, expensive, best_in, costs)


def scan(pool_a: Pool, pool_b: Pool, costs: Costs) -> dict | None:
    """Identify direction, solve for size, and decide if it's actionable."""
    if abs(pool_a.price - pool_b.price) / min(pool_a.price, pool_b.price) < 1e-6:
        return None
    cheap, expensive = (pool_a, pool_b) if pool_a.price < pool_b.price else (pool_b, pool_a)
    size, profit = optimal_trade(cheap, expensive, costs)
    gap_pct = (expensive.price / cheap.price - 1) * 100
    actionable = profit >= costs.min_profit_quote and size > 0
    return {
        "buy_in": cheap.name,
        "sell_in": expensive.name,
        "gap_pct": gap_pct,
        "trade_quote_in": size,
        "net_profit": profit,
        "roi_pct": (profit / size * 100) if size > 1e-6 else 0.0,
        "actionable": actionable,
    }


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def report(label: str, a: Pool, b: Pool, r: dict | None) -> None:
    print(f"\n=== {label} ===")
    print(f"  {a.name:14s} price = {a.price:.6f}   (depth {a.quote_reserve:,.0f} quote)")
    print(f"  {b.name:14s} price = {b.price:.6f}   (depth {b.quote_reserve:,.0f} quote)")
    if r is None:
        print("  No meaningful price gap.")
        return
    print(f"  Gap: {r['gap_pct']:.2f}%  ->  buy in {r['buy_in']}, sell in {r['sell_in']}")
    print(f"  Optimal size: {r['trade_quote_in']:.4f} quote in")
    flag = "ACTIONABLE ✅" if r["actionable"] else "skip (below threshold) ⏭"
    print(f"  Net profit: {r['net_profit']:.4f} quote   ROI {r['roi_pct']:.1f}%   {flag}")


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #
def builtin_scenarios() -> None:
    costs = Costs(priority_fee_quote=0.5, min_profit_quote=1.0)

    # 1. Tiny everyday gap — usually NOT worth it after fees.
    a = Pool("Raydium", base_reserve=1_000_000, quote_reserve=500_000, fee=0.0025)
    b = Pool("Orca",    base_reserve=1_000_000, quote_reserve=501_000, fee=0.0030)
    report("Scenario 1: small 0.2% gap", a, b, scan(a, b, costs))

    # 2. ANB-style dislocation: one pool got dumped 99%, the other lags.
    #    This is the kind of window the viral bots hit.
    crashed = Pool("Meteora-DAMM", base_reserve=9_000_000_000, quote_reserve=900,  fee=0.0025)
    stale   = Pool("Meteora-DLMM", base_reserve=100_000_000,   quote_reserve=10_000, fee=0.0025)
    report("Scenario 2: ANB-style 99% dislocation", crashed, stale, scan(crashed, stale, costs))

    # 3. Same gap but a fat priority-fee war erases the edge.
    costs_war = Costs(priority_fee_quote=2_000.0, min_profit_quote=1.0)
    report("Scenario 3: same gap, brutal priority-fee war", crashed, stale,
           scan(crashed, stale, costs_war))


def watch_loop(iterations: int = 20, delay: float = 1.0) -> None:
    """Simulate two pools drifting; occasionally inject a dislocation."""
    costs = Costs(priority_fee_quote=0.5, min_profit_quote=1.0)
    a = Pool("PoolA", 1_000_000, 500_000, 0.0025)
    b = Pool("PoolB", 1_000_000, 500_000, 0.0025)
    for i in range(iterations):
        # random walk on reserves
        a.quote_reserve *= 1 + random.uniform(-0.01, 0.01)
        b.quote_reserve *= 1 + random.uniform(-0.01, 0.01)
        if random.random() < 0.15:  # ~15% chance of a shock
            b.quote_reserve *= random.choice([0.5, 1.8])
        r = scan(a, b, costs)
        ts = time.strftime("%H:%M:%S")
        if r and r["actionable"]:
            print(f"[{ts}] tick {i:02d}  GAP {r['gap_pct']:5.2f}%  "
                  f"size {r['trade_quote_in']:.1f}  profit {r['net_profit']:.2f} ✅")
        else:
            gap = r["gap_pct"] if r else 0.0
            print(f"[{ts}] tick {i:02d}  gap {gap:5.2f}%  no trade")
        time.sleep(delay)


# --------------------------------------------------------------------------- #
# LIVE WIRING — intentionally left as an opt-in stub.
# --------------------------------------------------------------------------- #
def fetch_live_quote_TODO():
    """
    To go from simulation to real detection (still no trading), you would:
      - Pull quotes from an aggregator (e.g. Jupiter quote API) or read pool
        reserves directly via an RPC node (Helius/Triton/QuickNode).
      - Replace the synthetic Pool objects with live reserves each tick.
    To actually EXECUTE (not provided here, and high-risk) you would additionally:
      - Build an atomic transaction (both legs in one tx) and submit via Jito
        bundles so it lands or reverts together — never leg-by-leg.
      - Handle priority-fee bidding, MEV competition, and revert/slippage guards.
    This prototype deliberately stops before any signing/execution code.
    """
    raise NotImplementedError("Live execution is intentionally not implemented.")


def main() -> None:
    p = argparse.ArgumentParser(description="Solana-style DEX arbitrage detector (dry-run prototype)")
    p.add_argument("--watch", action="store_true", help="run a simulated drifting-pools loop")
    p.add_argument("--ticks", type=int, default=20, help="number of ticks in watch mode")
    args = p.parse_args()

    print("Solana-style DEX Arbitrage Detector — PROTOTYPE (no live trading)")
    if args.watch:
        watch_loop(iterations=args.ticks)
    else:
        builtin_scenarios()
    print("\nReminder: this is a model. Real edges are won by infra, not ideas.")


if __name__ == "__main__":
    main()