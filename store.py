"""
Tiny SQLite persistence layer for scan history (standard library only).

Every scan row is appended to scans.db so history survives restarts and can be
analyzed / backtested. Read-only market data — nothing here trades.
"""

from __future__ import annotations

import os
import sqlite3
import time

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scans.db")


def _conn(path: str = DB) -> sqlite3.Connection:
    c = sqlite3.connect(path)
    c.execute(
        """CREATE TABLE IF NOT EXISTS scans(
            ts TEXT, symbol TEXT, n_dex INTEGER, comparable INTEGER,
            buy_dex TEXT, sell_dex TEXT, gap_pct REAL, net REAL)"""
    )
    return c


def record(results: list[dict], ts: str | None = None, path: str = DB) -> int:
    """Append one scan's rows. Returns number of rows written."""
    ts = ts or time.strftime("%Y-%m-%d %H:%M:%S")
    c = _conn(path)
    with c:
        for r in results:
            c.execute(
                "INSERT INTO scans VALUES (?,?,?,?,?,?,?,?)",
                (ts, r.get("symbol"), int(r.get("n_dex", 0)),
                 1 if r.get("comparable") else 0, r.get("buy_dex"), r.get("sell_dex"),
                 r.get("gap_pct"), r.get("net")),
            )
    n = len(results)
    c.close()
    return n


def load(limit: int = 5000, path: str = DB) -> list[tuple]:
    """Most-recent rows: (ts, symbol, n_dex, comparable, buy_dex, sell_dex, gap_pct, net)."""
    c = _conn(path)
    rows = c.execute(
        "SELECT ts,symbol,n_dex,comparable,buy_dex,sell_dex,gap_pct,net "
        "FROM scans ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    return rows


def stats(path: str = DB) -> dict:
    """Aggregate summary across all recorded scans."""
    c = _conn(path)
    q = c.execute
    total = q("SELECT COUNT(*) FROM scans").fetchone()[0]
    comparable = q("SELECT COUNT(*) FROM scans WHERE comparable=1").fetchone()[0]
    profitable = q("SELECT COUNT(*) FROM scans WHERE net IS NOT NULL AND net>0").fetchone()[0]
    # Best case: trade only the winners. Realistic: trade every comparable signal.
    pnl_winners = q("SELECT COALESCE(SUM(net),0) FROM scans WHERE net>0").fetchone()[0]
    pnl_all = q("SELECT COALESCE(SUM(net),0) FROM scans WHERE comparable=1").fetchone()[0]
    span = q("SELECT MIN(ts), MAX(ts) FROM scans").fetchone()
    c.close()
    return {"total_rows": total, "comparable": comparable, "profitable": profitable,
            "pnl_if_winners_only": pnl_winners, "pnl_if_traded_all": pnl_all,
            "first": span[0], "last": span[1]}


def clear(path: str = DB) -> None:
    c = _conn(path)
    with c:
        c.execute("DELETE FROM scans")
    c.close()
