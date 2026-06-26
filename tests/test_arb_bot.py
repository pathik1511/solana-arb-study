"""Unit tests for the pure arbitrage math in arb_bot.py (no network, offline)."""

import arb_bot
from arb_bot import Pool, Costs, round_trip_profit, optimal_trade, scan


def test_price_is_quote_over_base():
    assert Pool("p", base_reserve=1000, quote_reserve=500).price == 0.5


def test_buy_base_returns_token_and_moves_reserves():
    pool = Pool("p", base_reserve=1_000_000, quote_reserve=500_000, fee=0.0)
    out = pool.buy_base(1000)
    assert out > 0
    # constant product preserved (no fee): k stays the same after a manual update
    k = pool.base_reserve * pool.quote_reserve
    new_quote = pool.quote_reserve + 1000
    assert abs((pool.base_reserve - out) * new_quote - k) < 1e-3


def test_round_trip_same_pool_loses_to_fees():
    pool = Pool("p", 1_000_000, 500_000, fee=0.003)
    # buy then immediately sell in the same pool must lose (spread + 2x fee)
    base = pool.buy_base(1000)
    back = pool.sell_base(base)
    assert back < 1000


def test_optimal_trade_finds_profit_on_dislocation():
    cheap = Pool("cheap", base_reserve=9_000_000_000, quote_reserve=900, fee=0.0025)
    dear = Pool("dear", base_reserve=100_000_000, quote_reserve=10_000, fee=0.0025)
    costs = Costs(priority_fee_quote=0.5, min_profit_quote=1.0)
    size, profit = optimal_trade(cheap, dear, costs)
    assert size > 0
    assert profit > 0


def test_optimal_trade_no_gap_is_not_profitable():
    a = Pool("a", 1_000_000, 500_000, fee=0.003)
    b = Pool("b", 1_000_000, 500_000, fee=0.003)
    costs = Costs(priority_fee_quote=0.5, min_profit_quote=1.0)
    _size, profit = optimal_trade(a, b, costs)
    assert profit <= 0


def test_scan_returns_none_for_equal_pools():
    a = Pool("a", 1_000_000, 500_000)
    b = Pool("b", 1_000_000, 500_000)
    assert scan(a, b, Costs()) is None


def test_scan_flags_actionable_on_big_gap_with_correct_direction():
    crashed = Pool("crashed", 9_000_000_000, 900, fee=0.0025)
    stale = Pool("stale", 100_000_000, 10_000, fee=0.0025)
    r = scan(crashed, stale, Costs(priority_fee_quote=0.5, min_profit_quote=1.0))
    assert r is not None
    assert r["actionable"] is True
    # cheaper pool (lower price) should be the buy venue
    assert r["buy_in"] == "crashed"
    assert r["sell_in"] == "stale"


def test_priority_fee_war_can_erase_edge():
    crashed = Pool("crashed", 9_000_000_000, 900, fee=0.0025)
    stale = Pool("stale", 100_000_000, 10_000, fee=0.0025)
    size, _ = optimal_trade(crashed, stale, Costs(priority_fee_quote=0.5))
    # with a huge priority fee, the same trade size is no longer profitable
    war = Costs(priority_fee_quote=50_000.0)
    assert round_trip_profit(crashed, stale, size, war) < 0


def test_module_exposes_expected_callables():
    for name in ("Pool", "Costs", "round_trip_profit", "optimal_trade", "scan"):
        assert hasattr(arb_bot, name)
