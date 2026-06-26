# Solana DEX Arbitrage — From Hype to Hard Numbers

[![CI](https://github.com/pathik1511/solana-arb-study/actions/workflows/ci.yml/badge.svg)](https://github.com/pathik1511/solana-arb-study/actions/workflows/ci.yml)

A staged, honest investigation into Solana DEX arbitrage bots: from the viral
"turned <\$1 into a fortune" stories, down to a real, latency-optimized execution
bot tested on devnet — and the empirical reasons retail arbitrage doesn't pay.

This started as a question ("can a home bot really do this?") and turned into a
working system plus a data-backed answer. Every claim here is something the code
actually measured, not assumed.

> **Educational project. No live trading, no real funds.** The execution bot runs
> only on Solana **devnet** (free test SOL). Nothing here signs a mainnet
> transaction or moves real money.

## TL;DR findings

- On **liquid pairs** (SOL/USDC across Raydium, Orca, Meteora), ~70 minutes of
  live scanning found **zero** profitable round-trips. Gaps stayed under ~0.2%
  and never beat fees + slippage.
- The viral wins happened on a **freshly-crashing illiquid token** (a 99% price
  dislocation), not on liquid markets — rare, and dominated by professional bots.
- A from-home execution bot lands a transaction in **~900ms** on a shared RPC.
  Optimized (cached blockhash, keep-alive, fast polling) the *pipeline* overhead
  drops to near-zero, but real confirmation is still bounded by network + block time.
- Pros co-locate beside validators and submit via TPU/Jito, landing **in-block at
  single-digit milliseconds** and winning a priority-fee auction. Retail can't
  match that from a residential connection. Expected value of a home arb bot is
  **negative**, not "small positive."

## The stages

### Stage 0 — `arb_bot.py` (offline model)
A constant-product (x·y=k) AMM model with two pools. Detects a price gap, solves
for the profit-maximizing trade size (golden-section search), subtracts realistic
costs, and decides if it clears a threshold. Includes an ANB-style 99% dislocation
scenario and a "priority-fee war" scenario that erases the edge. Pure standard
library.

```bash
python arb_bot.py            # built-in scenarios
python arb_bot.py --watch    # simulated drifting pools
```

### Stage 1 — `live_scanner.py` (live, read-only)
Reads **real** Solana prices via Jupiter's public quote API, isolates per-DEX
prices, and simulates a real round-trip (buy cheap, sell dear) minus an estimated
priority fee. No wallet, no trading. Handles rate-limiting with backoff. Pure
standard library.

```bash
python live_scanner.py --usd 20            # scan SOL/USDC across 3 DEXs
python live_scanner.py --usd 20 --once     # single scan
```

**Result:** across a long live run, every gap came back negative after costs.

### Stage 2 — `stage2_devnet_bot.py` (real execution, devnet)
Detection on real mainnet prices; **execution on devnet** with a real signed
transaction, instrumented for `build → sign → submit → confirm` latency. Generates
and persists a throwaway devnet keypair, funds via faucet (with web-faucet
fallback), and refuses any non-devnet RPC.

```bash
python stage2_devnet_bot.py --fund         # create/fund the devnet key (once)
python stage2_devnet_bot.py --cycles 5     # detect + execute, timed
```

**Result:** ~936ms average end-to-end landing on the public devnet RPC.

### Stage 2.5 — `stage2_fast.py` (latency-optimized, devnet)
Same job, four optimizations: keep-alive HTTPS connection, background-cached
blockhash off the critical path, 100ms confirmation polling, and `skipPreflight`.

```bash
python stage2_fast.py --cycles 5
python stage2_fast.py --cycles 5 --rpc <your-devnet-rpc>
```

**Result:** `build+sign` → ~0ms, `submit` → ~26ms (from ~100ms). Honest caveat:
re-sending an identical transaction reuses the same signature, so repeat cycles
can *appear* to confirm in ~30ms — that's a status re-read of an already-landed
tx, not a fresh landing. True fresh landing remains ~900ms on a shared RPC.

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv
# Windows:  .\.venv\Scripts\activate     macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt          # only Stage 2/2.5 need a dependency (solders)
```

Stages 0 and 1 have **no third-party dependencies**.

## Safety notes

- `devnet_key.json` is git-ignored and must **never** be committed or shared.
- Never put a real wallet's seed phrase or private key anywhere in this project.
- The devnet bots refuse any RPC URL that isn't devnet.

## Tests & CI

The pure arbitrage math (AMM pricing, the profit-maximizing trade-size solver,
gap detection) is unit-tested with `pytest` — no network required, so it runs
fast and deterministically in CI.

```bash
pip install -r requirements-dev.txt
pytest            # run the unit tests
ruff check .      # lint
```

Every push runs `ruff` + `pytest` via GitHub Actions (`.github/workflows/ci.yml`).

## What this project is really about

Not a money-maker — a worked demonstration of why a market is efficient, and a
clean example of building, measuring, and optimizing a real-time execution
pipeline end to end. The valuable output is the engineering and the evidence, not
a trading edge.

## License

MIT — see `LICENSE`.
