#!/usr/bin/env python3
r"""
STAGE 2 — Devnet Execution Bot (real network, test money, ZERO financial risk)
==============================================================================

What this proves that Stage 1 could not: whether your transaction can actually
be BUILT, SIGNED, SENT, and CONFIRMED fast enough to matter. It runs the full
execution pipeline on Solana **devnet** (free test SOL), and times every phase.

Honest note on what's real here:
  * DETECTION uses REAL mainnet prices (live, via Jupiter) — same as Stage 1.
  * EXECUTION runs on DEVNET, because devnet has no real DEX liquidity to
    arbitrage. So instead of a fake "devnet arb", we execute a real, signed
    devnet transaction (a tiny self-transfer that stands in for a trade leg)
    and MEASURE how long it takes to land. That landing time is the whole
    lesson: compare it to how fast a real gap closes.

SAFETY (read this):
  * This ONLY ever talks to devnet. It refuses non-devnet RPC URLs.
  * It generates its OWN throwaway devnet keypair (devnet_key.json). The SOL it
    holds is worthless test money.
  * NEVER paste a real wallet's seed phrase or private key into this project.
    Real funds have no business anywhere near a learning script.

Requires: solders   ->   .\.venv\Scripts\python.exe -m pip install solders

Usage:
    python stage2_devnet_bot.py --fund          # create/fund the devnet key
    python stage2_devnet_bot.py --cycles 5      # detect + execute 5 times, timed
    python stage2_devnet_bot.py --rpc <devnet-rpc-url>   # use your own devnet RPC
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from solders.keypair import Keypair
from solders.system_program import transfer, TransferParams
from solders.message import MessageV0
from solders.transaction import VersionedTransaction
from solders.hash import Hash

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
DEFAULT_RPC = "https://api.devnet.solana.com"
KEYFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "devnet_key.json")

# Mainnet price source for DETECTION (read-only, no trading).
JUP = "https://lite-api.jup.ag/swap/v1/quote"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL = "So11111111111111111111111111111111111111112"
DEXES = ["Raydium", "Orca", "Meteora"]


# --------------------------------------------------------------------------- #
# Devnet RPC (raw JSON-RPC over stdlib; solders only builds/signs the tx)
# --------------------------------------------------------------------------- #
class Rpc:
    def __init__(self, url: str):
        if "devnet" not in url:
            raise SystemExit(f"REFUSING non-devnet RPC for safety: {url}\n"
                             "This learning bot only runs on devnet.")
        self.url = url

    def call(self, method: str, params: list):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def balance(self, pubkey) -> int:
        return self.call("getBalance", [str(pubkey)])["result"]["value"]

    def latest_blockhash(self) -> str:
        # Use "finalized": a finalized blockhash is known by every node in the
        # public load-balanced pool, avoiding "Blockhash not found" at send time.
        return self.call("getLatestBlockhash", [{"commitment": "finalized"}])["result"]["value"]["blockhash"]


# --------------------------------------------------------------------------- #
# Keypair persistence (devnet throwaway only)
# --------------------------------------------------------------------------- #
def load_or_create_keypair() -> Keypair:
    if os.path.exists(KEYFILE):
        with open(KEYFILE) as f:
            secret = bytes(json.load(f))
        return Keypair.from_bytes(secret)
    kp = Keypair()
    with open(KEYFILE, "w") as f:
        json.dump(list(bytes(kp)), f)
    print(f"Created a NEW devnet keypair -> {os.path.basename(KEYFILE)} (test money only)")
    return kp


# --------------------------------------------------------------------------- #
# Funding: try RPC airdrop; if the (notoriously rate-limited) faucet fails,
# fall back to instructing the user to use the web faucet, then poll balance.
# --------------------------------------------------------------------------- #
def ensure_funded(rpc: Rpc, kp: Keypair, min_lamports: int = 20_000_000) -> bool:
    pk = kp.pubkey()
    bal = rpc.balance(pk)
    print(f"Devnet address: {pk}")
    print(f"Current balance: {bal/1e9:.4f} SOL")
    if bal >= min_lamports:
        return True

    print("Balance low — trying the RPC airdrop faucet (often rate-limited)...")
    for attempt in range(3):
        try:
            r = rpc.call("requestAirdrop", [str(pk), 100_000_000])  # 0.1 SOL
            if r.get("error"):
                print(f"  airdrop attempt {attempt+1}: {r['error'].get('message')}")
            else:
                print(f"  airdrop requested (sig {str(r.get('result'))[:8]}...), waiting for funds...")
                for _ in range(20):
                    if rpc.balance(pk) > 0:
                        break
                    time.sleep(1)
        except urllib.error.HTTPError as e:
            print(f"  airdrop attempt {attempt+1}: HTTP {e.code} (faucet rate-limited)")
        if rpc.balance(pk) > 0:
            break
        time.sleep(2)

    bal = rpc.balance(pk)
    if bal > 0:
        print(f"Funded. Balance: {bal/1e9:.4f} SOL")
        return True

    # Web-faucet fallback — the reliable path when the RPC faucet is exhausted.
    print("\nRPC faucet is exhausted (very common). Fund it manually, once:")
    print("  1. Open https://faucet.solana.com")
    print(f"  2. Paste this address:  {pk}")
    print("  3. Choose Devnet, request 0.5–1 SOL.")
    print("Waiting up to 3 minutes for the funds to arrive (Ctrl+C to abort)...")
    for _ in range(90):
        if rpc.balance(pk) > 0:
            print(f"Detected funds. Balance: {rpc.balance(pk)/1e9:.4f} SOL")
            return True
        time.sleep(2)
    print("No funds detected. Re-run with --fund after the faucet sends SOL.")
    return False


# --------------------------------------------------------------------------- #
# Detection: real mainnet best gap (read-only), reused from Stage 1 logic
# --------------------------------------------------------------------------- #
def jup_quote(in_mint: str, out_mint: str, amount: int, dex: str):
    params = {"inputMint": in_mint, "outputMint": out_mint, "amount": str(amount),
              "slippageBps": "50", "dexes": dex, "onlyDirectRoutes": "true"}
    url = JUP + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=15) as r:
            d = json.loads(r.read())
        return int(d["outAmount"]) if "outAmount" in d else None
    except Exception:
        return None


def best_gap(usd: float = 20.0):
    base = int(usd * 1e6)
    prices = {}
    for dex in DEXES:
        out = jup_quote(USDC, SOL, base, dex)
        if out:
            prices[dex] = usd / (out / 1e9)  # USDC per SOL
        time.sleep(0.2)
    if len(prices) < 2:
        return None
    cheap, dear = min(prices, key=prices.get), max(prices, key=prices.get)
    return {"cheap": cheap, "dear": dear, "gap_pct": (prices[dear]/prices[cheap]-1)*100}


# --------------------------------------------------------------------------- #
# Execution: build -> sign -> send -> confirm a real devnet tx, timed.
# --------------------------------------------------------------------------- #
def execute_timed(rpc: Rpc, kp: Keypair) -> dict | None:
    pk = kp.pubkey()
    for retry in range(3):
        try:
            t0 = time.time()
            bh = rpc.latest_blockhash()
            ix = transfer(TransferParams(from_pubkey=pk, to_pubkey=pk, lamports=1000))
            msg = MessageV0.try_compile(pk, [ix], [], Hash.from_string(bh))
            tx = VersionedTransaction(msg, [kp])
            t1 = time.time()  # build+sign done
            raw = base64.b64encode(bytes(tx)).decode()
            send = rpc.call("sendTransaction", [raw, {"encoding": "base64"}])
            t2 = time.time()  # submitted
            sig = send.get("result")
            if not sig:
                err = send.get("error", {}).get("message", "")
                if "Blockhash" in err and retry < 2:
                    continue  # fetch a fresh blockhash and retry
                print(f"  send failed: {err}")
                return None
            for _ in range(60):
                s = rpc.call("getSignatureStatuses", [[sig]])["result"]["value"][0]
                if s and s.get("confirmationStatus") in ("confirmed", "finalized"):
                    t3 = time.time()
                    return {"build_ms": (t1-t0)*1000, "submit_ms": (t2-t1)*1000,
                            "confirm_ms": (t3-t2)*1000, "total_ms": (t3-t0)*1000, "sig": sig}
                time.sleep(0.3)
            print("  submitted but not confirmed within window")
            return None
        except urllib.error.HTTPError as e:
            print(f"  RPC HTTP {e.code} — retrying" if retry < 2 else f"  RPC HTTP {e.code}")
            time.sleep(1)
    return None


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    p = argparse.ArgumentParser(description="Stage 2: devnet execution bot (test money, zero risk)")
    p.add_argument("--rpc", default=DEFAULT_RPC, help="devnet RPC url")
    p.add_argument("--fund", action="store_true", help="just create/fund the devnet keypair and exit")
    p.add_argument("--cycles", type=int, default=3, help="detect+execute cycles to run")
    p.add_argument("--no-detect", action="store_true", help="skip mainnet detection, just time execution")
    args = p.parse_args()

    print("=" * 70)
    print("STAGE 2 — Devnet Execution Bot   (DEVNET TEST MONEY ONLY — no real funds)")
    print("=" * 70)
    rpc = Rpc(args.rpc)
    kp = load_or_create_keypair()

    if not ensure_funded(rpc, kp):
        return
    if args.fund:
        print("Funded and ready. Re-run without --fund to detect + execute.")
        return

    latencies = []
    for i in range(args.cycles):
        print(f"\n--- Cycle {i+1}/{args.cycles} ---")
        if not args.no_detect:
            g = best_gap()
            if g:
                print(f"[mainnet] best live gap: {g['gap_pct']:.3f}%  "
                      f"(buy {g['cheap']}, sell {g['dear']})")
            else:
                print("[mainnet] detection unavailable this tick (rate-limited)")
        print("[devnet] executing a real signed transaction, timing the pipeline...")
        r = execute_timed(rpc, kp)
        if r:
            latencies.append(r["total_ms"])
            print(f"  build+sign {r['build_ms']:.0f}ms | submit {r['submit_ms']:.0f}ms | "
                  f"confirm {r['confirm_ms']:.0f}ms | TOTAL {r['total_ms']:.0f}ms")
        time.sleep(1)

    if latencies:
        avg = sum(latencies) / len(latencies)
        print("\n" + "=" * 70)
        print(f"Average end-to-end landing time: {avg:.0f} ms over {len(latencies)} tx")
        print("THE LESSON: a real arbitrage gap on a liquid pair is typically gone")
        print("within ~1 block (~400ms) as pro bots close it. Compare that to your")
        print("landing time above. That delta is why retail arbitrage rarely wins.")
        print("=" * 70)


if __name__ == "__main__":
    main()
