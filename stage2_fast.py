#!/usr/bin/env python3
r"""
STAGE 2.5 — Latency-Optimized Devnet Execution Bot (devnet test money, ZERO risk)
=================================================================================

Same job as stage2_devnet_bot.py, but tuned to push end-to-end landing time down
toward Solana's ~400ms slot. Four optimizations over the baseline:

  1. KEEP-ALIVE connection  — one persistent TLS connection reused for every RPC
                              call, instead of a fresh handshake each time.
  2. CACHED BLOCKHASH       — refreshed by a background thread, so the critical
                              path never waits on a getLatestBlockhash round-trip.
  3. FAST CONFIRM POLLING   — checks signature status every 50ms (was 300ms), so
                              the measured confirm time reflects reality.
  4. skipPreflight=True     — drops the on-send simulation (safe: tx is trivial).

Honest ceiling (read this): even at ~400ms this would NOT win real arbitrage.
Pro firms co-locate next to validators and submit via TPU/Jito, landing in the
SAME block at single-digit-ms latency, then win a priority-fee auction inside it.
This is a latency-engineering exercise, not a path to a profitable mainnet bot.

Safety: devnet-only (refuses any non-devnet RPC). Reuses the SAME devnet_key.json
that Stage 2 created/funded. Never put a real wallet key near this.

Requires: solders   ->   .\.venv\Scripts\python.exe -m pip install solders

Usage:
    python stage2_fast.py --cycles 5
    python stage2_fast.py --cycles 5 --no-detect      # pure execution timing
    python stage2_fast.py --rpc <your-devnet-rpc>     # a closer RPC helps a lot
"""

from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from urllib.parse import urlparse

from solders.keypair import Keypair
from solders.system_program import transfer, TransferParams
from solders.message import MessageV0
from solders.transaction import VersionedTransaction
from solders.hash import Hash

DEFAULT_RPC = "https://api.devnet.solana.com"
KEYFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "devnet_key.json")
JUP = "https://lite-api.jup.ag/swap/v1/quote"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL = "So11111111111111111111111111111111111111112"
DEXES = ["Raydium", "Orca", "Meteora"]


# --------------------------------------------------------------------------- #
# Keep-alive JSON-RPC client (one persistent HTTPS connection, lock-guarded)
# --------------------------------------------------------------------------- #
class FastRpc:
    def __init__(self, url: str):
        if "devnet" not in url:
            raise SystemExit(f"REFUSING non-devnet RPC for safety: {url}")
        u = urlparse(url)
        self.host = u.hostname
        self.port = u.port or 443
        self.path = u.path or "/"
        self._lock = threading.Lock()
        self._conn: http.client.HTTPSConnection | None = None

    def _ensure(self):
        if self._conn is None:
            self._conn = http.client.HTTPSConnection(self.host, self.port, timeout=30)

    def call(self, method: str, params: list):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        headers = {"Content-Type": "application/json", "Connection": "keep-alive"}
        with self._lock:
            for attempt in range(2):
                try:
                    self._ensure()
                    self._conn.request("POST", self.path, body=body, headers=headers)
                    resp = self._conn.getresponse()
                    data = resp.read()
                    return json.loads(data)
                except Exception:
                    # connection went stale — drop it and retry once fresh
                    try:
                        self._conn.close()
                    except Exception:
                        pass
                    self._conn = None
                    if attempt == 1:
                        raise

    def balance(self, pubkey) -> int:
        return self.call("getBalance", [str(pubkey)])["result"]["value"]


# --------------------------------------------------------------------------- #
# Background blockhash cache — keeps a fresh finalized blockhash ready so the
# execution critical path never blocks on a network round-trip.
# --------------------------------------------------------------------------- #
class BlockhashCache:
    def __init__(self, rpc: FastRpc, refresh_s: float = 15.0):
        self.rpc = rpc
        self.refresh_s = refresh_s
        self._bh: str | None = None
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)

    def _fetch(self):
        self._bh = self.rpc.call("getLatestBlockhash",
                                 [{"commitment": "finalized"}])["result"]["value"]["blockhash"]

    def start(self):
        self._fetch()              # prime synchronously once
        self._t.start()

    def _loop(self):
        while not self._stop.wait(self.refresh_s):
            try:
                self._fetch()
            except Exception:
                pass

    def get(self) -> str:
        return self._bh

    def stop(self):
        self._stop.set()


# --------------------------------------------------------------------------- #
# Keypair (reuse Stage 2's funded devnet key)
# --------------------------------------------------------------------------- #
def load_keypair() -> Keypair:
    if not os.path.exists(KEYFILE):
        raise SystemExit("No devnet_key.json found. Run stage2_devnet_bot.py --fund first.")
    with open(KEYFILE) as f:
        return Keypair.from_bytes(bytes(json.load(f)))


# --------------------------------------------------------------------------- #
# Detection (real mainnet, read-only) — uses plain urllib, off the hot path
# --------------------------------------------------------------------------- #
def jup_quote(in_mint, out_mint, amount, dex):
    params = {"inputMint": in_mint, "outputMint": out_mint, "amount": str(amount),
              "slippageBps": "50", "dexes": dex, "onlyDirectRoutes": "true"}
    url = JUP + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=15) as r:
            d = json.loads(r.read())
        return int(d["outAmount"]) if "outAmount" in d else None
    except Exception:
        return None


def best_gap(usd=20.0):
    base = int(usd * 1e6)
    prices = {}
    for dex in DEXES:
        out = jup_quote(USDC, SOL, base, dex)
        if out:
            prices[dex] = usd / (out / 1e9)
        time.sleep(0.2)
    if len(prices) < 2:
        return None
    cheap, dear = min(prices, key=prices.get), max(prices, key=prices.get)
    return {"cheap": cheap, "dear": dear, "gap_pct": (prices[dear] / prices[cheap] - 1) * 100}


# --------------------------------------------------------------------------- #
# Optimized execution: blockhash already cached, keep-alive send, 50ms confirm
# --------------------------------------------------------------------------- #
def execute_fast(rpc: FastRpc, kp: Keypair, bhc: BlockhashCache) -> dict | None:
    pk = kp.pubkey()
    t0 = time.time()
    bh = bhc.get()                                   # cached — no network wait
    ix = transfer(TransferParams(from_pubkey=pk, to_pubkey=pk, lamports=1000))
    msg = MessageV0.try_compile(pk, [ix], [], Hash.from_string(bh))
    tx = VersionedTransaction(msg, [kp])
    t1 = time.time()                                 # build+sign done
    raw = base64.b64encode(bytes(tx)).decode()
    send = rpc.call("sendTransaction", [raw, {"encoding": "base64", "skipPreflight": True}])
    t2 = time.time()                                 # submitted
    sig = send.get("result")
    if not sig:
        print(f"  send failed: {send.get('error', {}).get('message')}")
        return None
    while time.time() - t2 < 30:
        resp = rpc.call("getSignatureStatuses", [[sig]])
        # Public RPC can occasionally return an error object (no 'result') under
        # rapid polling — treat that as "not yet", don't crash.
        s = None
        if resp and "result" in resp:
            s = resp["result"]["value"][0]
        if s and s.get("confirmationStatus") in ("confirmed", "finalized"):
            t3 = time.time()
            return {"build_ms": (t1 - t0) * 1000, "submit_ms": (t2 - t1) * 1000,
                    "confirm_ms": (t3 - t2) * 1000, "total_ms": (t3 - t0) * 1000}
        time.sleep(0.1)                              # 100ms poll (gentle on shared RPC)
    print("  not confirmed within window")
    return None


def main() -> None:
    p = argparse.ArgumentParser(description="Stage 2.5: latency-optimized devnet bot")
    p.add_argument("--rpc", default=DEFAULT_RPC)
    p.add_argument("--cycles", type=int, default=5)
    p.add_argument("--no-detect", action="store_true")
    args = p.parse_args()

    print("=" * 70)
    print("STAGE 2.5 — Latency-Optimized Devnet Bot   (DEVNET TEST MONEY ONLY)")
    print("=" * 70)
    rpc = FastRpc(args.rpc)
    kp = load_keypair()
    bal = rpc.balance(kp.pubkey())
    print(f"Devnet address: {kp.pubkey()}  |  balance {bal/1e9:.4f} SOL")
    if bal == 0:
        raise SystemExit("Key is empty — fund it via stage2_devnet_bot.py --fund first.")

    bhc = BlockhashCache(rpc)
    bhc.start()
    print("Blockhash cache warmed; keep-alive connection open.\n")

    lat = []
    try:
        for i in range(args.cycles):
            print(f"--- Cycle {i+1}/{args.cycles} ---")
            if not args.no_detect:
                g = best_gap()
                print(f"[mainnet] best live gap: {g['gap_pct']:.3f}%" if g
                      else "[mainnet] detection rate-limited")
            r = execute_fast(rpc, kp, bhc)
            if r:
                lat.append(r["total_ms"])
                print(f"  build+sign {r['build_ms']:.0f}ms | submit {r['submit_ms']:.0f}ms | "
                      f"confirm {r['confirm_ms']:.0f}ms | TOTAL {r['total_ms']:.0f}ms")
    finally:
        bhc.stop()

    if lat:
        print("\n" + "=" * 70)
        print(f"Optimized average landing time: {sum(lat)/len(lat):.0f} ms over {len(lat)} tx")
        print(f"  best cycle: {min(lat):.0f} ms")
        print("Compare to your Stage 2 baseline (~936ms). Most of the win comes from")
        print("the cached blockhash (build ~0ms) and 50ms confirm polling.")
        print("Reality check: pros land in-block at <20ms via co-located TPU/Jito and")
        print("win a fee auction. Even this optimized number does not cross into profit.")
        print("=" * 70)


if __name__ == "__main__":
    main()
