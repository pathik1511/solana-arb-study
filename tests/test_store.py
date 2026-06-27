"""Unit tests for the SQLite persistence layer (uses a temp DB, no network)."""

import os
import tempfile

import store


def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # let sqlite create it fresh
    return path


def test_record_and_load_roundtrip():
    p = _tmp_db()
    try:
        rows = [{"symbol": "SOL", "n_dex": 3, "comparable": True, "buy_dex": "Raydium",
                 "sell_dex": "Phoenix", "gap_pct": 3.5, "net": -1.42},
                {"symbol": "JUP", "n_dex": 1, "comparable": False}]
        assert store.record(rows, ts="2026-06-26 10:00:00", path=p) == 2
        loaded = store.load(path=p)
        assert len(loaded) == 2
    finally:
        os.remove(p)


def test_stats_pnl_math():
    p = _tmp_db()
    try:
        store.record([
            {"symbol": "A", "n_dex": 2, "comparable": True, "buy_dex": "X",
             "sell_dex": "Y", "gap_pct": 1.0, "net": -0.5},
            {"symbol": "B", "n_dex": 2, "comparable": True, "buy_dex": "X",
             "sell_dex": "Y", "gap_pct": 2.0, "net": 0.2},
            {"symbol": "C", "n_dex": 0, "comparable": False},
        ], ts="2026-06-26 10:00:00", path=p)
        s = store.stats(p)
        assert s["total_rows"] == 3
        assert s["comparable"] == 2
        assert s["profitable"] == 1
        assert abs(s["pnl_if_winners_only"] - 0.2) < 1e-9
        assert abs(s["pnl_if_traded_all"] - (-0.3)) < 1e-9
    finally:
        os.remove(p)


def test_clear_empties_table():
    p = _tmp_db()
    try:
        store.record([{"symbol": "A", "n_dex": 2, "comparable": True, "buy_dex": "X",
                       "sell_dex": "Y", "gap_pct": 1.0, "net": 0.1}], path=p)
        store.clear(p)
        assert store.load(path=p) == []
    finally:
        os.remove(p)
