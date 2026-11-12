"""Backtester (M2) scenario tests with hand-computed expectations, plus determinism and
look-ahead guards on synthetic streams. Model: engine/include/tickforge/sim.hpp, ADR 0006."""
from __future__ import annotations

import pytest

from tickforge import itch, load_engine, synth

eng = load_engine()
L = 1
BID, ASK = 1_000_000, 1_000_100  # $100.00 / $100.01


class Script(eng.Strategy):
    """Submits a fixed list of actions on the first N market callbacks."""

    def __init__(self, actions):
        super().__init__()
        self.actions = list(actions)
        self.ids = []
        self.fills = []

    def on_market(self, sim):
        if self.actions:
            act = self.actions.pop(0)
            if act is None:
                return
            kind, *args = act
            if kind == "submit":
                self.ids.append(sim.submit(*args))
            elif kind == "cancel":
                sim.cancel(self.ids[args[0]])

    def on_fill(self, sim, f):
        self.fills.append((f.ts, f.side, f.price, f.qty, f.maker))


def cfg(**kw):
    c = eng.SimConfig()
    c.locate = L
    c.latency_ns = 10
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def run(msgs, strategy, **kw):
    bt = eng.Backtest(cfg(**kw), strategy)
    buf = b"".join(msgs)
    assert bt.feed(buf) == len(buf)
    bt.finish()
    return bt


def test_passive_fill_uses_exact_queue_position():
    s = Script([("submit", "B", BID, 100)])
    bt = run([
        itch.add(1000, L, 1, "B", 100, "T", BID),
        itch.add(1000, L, 2, "S", 100, "T", ASK),
        itch.add(2000, L, 3, "B", 200, "T", BID),   # our order arrived at 1010: ref 3 is behind us
        itch.executed(3000, L, 1, 60, 1),           # ahead 100 -> 40
        itch.cancel(4000, L, 1, 40),                # ahead -> 0, still no fill
        itch.executed(5000, L, 3, 50, 2),           # queue reached the order behind us -> we fill 50
        itch.executed(6000, L, 3, 100, 3),          # remaining 50
    ], s)
    assert s.fills == [(5000, "B", BID, 50, True), (6000, "B", BID, 50, True)]
    assert bt.position == 100
    assert bt.fees == -20 * 100
    assert bt.cash == -100 * BID + 2000
    assert bt.open_orders == []


def test_order_ahead_cancelled_reduces_ahead_without_fill():
    s = Script([("submit", "B", BID, 100)])
    bt = run([
        itch.add(1000, L, 1, "B", 300, "T", BID),
        itch.add(1000, L, 2, "S", 100, "T", ASK),
        itch.add(2000, L, 9, "S", 100, "T", ASK + 100),
        itch.delete(3000, L, 1),
    ], s)
    assert s.fills == []
    (o,) = bt.open_orders
    assert o.ahead == 0 and o.qty == 100


def test_aggressive_ioc_walks_visible_levels_with_taker_fee():
    s = Script([None, None, ("submit", "B", ASK + 100, 150, True)])
    bt = run([
        itch.add(1000, L, 1, "B", 100, "T", BID),
        itch.add(1000, L, 2, "S", 100, "T", ASK),
        itch.add(1000, L, 3, "S", 100, "T", ASK + 100),
        itch.add(2000, L, 4, "B", 100, "T", BID - 100),  # triggers arrival at 1010
    ], s)
    assert s.fills == [(1010, "B", ASK, 100, False), (1010, "B", ASK + 100, 50, False)]
    assert bt.fees == 30 * 150
    assert bt.cash == -(100 * ASK + 50 * (ASK + 100)) - 30 * 150
    assert bt.open_orders == []  # IOC remainder discarded


def test_trade_through_fills_resting_order_fully():
    s = Script([None, ("submit", "B", BID - 100, 200)])
    run([
        itch.add(1000, L, 1, "B", 500, "T", BID),
        itch.add(1000, L, 2, "S", 100, "T", ASK),
        itch.add(2000, L, 3, "B", 100, "T", BID - 200),
        itch.executed(3000, L, 3, 100, 1),  # a worse-priced bid traded: we would have been hit first
    ], s)
    assert s.fills == [(3000, "B", BID - 100, 200, True)]


def test_opposite_side_reaching_our_price_fills_us():
    s = Script([None, ("submit", "S", ASK + 200, 100)])
    run([
        itch.add(1000, L, 1, "B", 100, "T", BID),
        itch.add(1000, L, 2, "S", 100, "T", ASK + 300),
        itch.add(2000, L, 3, "B", 100, "T", ASK + 200),  # bid arrives at our ask price
    ], s)
    assert s.fills == [(2000, "S", ASK + 200, 100, True)]


def test_cancel_racing_a_fill_is_rejected_by_latency():
    s = Script([("submit", "B", BID, 100), None, ("cancel", 0)])
    bt = run([
        itch.add(1000, L, 1, "S", 100, "T", ASK),
        itch.add(1000, L, 2, "B", 100, "T", BID - 100),
        itch.add(2000, L, 3, "B", 100, "T", BID),        # our bid rested at 1010, ref 3 behind us
        itch.executed(2005, L, 3, 100, 1),               # cancel decided at 2000 arrives at 2010: too late
        itch.add(3000, L, 4, "B", 100, "T", BID - 300),
    ], s)
    assert [f[3] for f in s.fills] == [100]
    assert bt.stats["cancel_rejects"] == 1


def test_risk_limit_rejects_orders_that_could_breach_position():
    s = Script([("submit", "B", BID, 100), ("submit", "B", BID, 1)])
    bt = run([
        itch.add(1000, L, 1, "S", 100, "T", ASK),
        itch.add(1000, L, 2, "B", 100, "T", BID),
    ], s, max_position=100)
    assert s.ids[0] > 0 and s.ids[1] == 0
    assert bt.stats["rejected"] == 1


def _touch_run(buf, **kw):
    bt = eng.Backtest(cfg(latency_ns=20_000, **kw), eng.TouchQuoter(100))
    bt.feed(buf)
    bt.finish()
    return bt


@pytest.fixture(scope="module")
def stream():
    return synth.generate(60_000, seed=21, n_stocks=2)


def test_touch_quoter_trades_and_accounting_identity(stream):
    bt = _touch_run(stream)
    fills = bt.fills
    assert len(fills) > 50
    cash = sum((-1 if f.side == "B" else 1) * f.price * f.qty - f.fee for f in fills)
    pos = sum((1 if f.side == "B" else -1) * f.qty for f in fills)
    assert cash == bt.cash and pos == bt.position
    assert abs(bt.position) <= 1000
    assert all(f.maker for f in fills)  # quoting at the touch never takes liquidity


def test_backtest_is_bit_reproducible(stream):
    a, b = _touch_run(stream, jitter_ns=5_000, seed=3), _touch_run(stream, jitter_ns=5_000, seed=3)
    assert a.fill_digest == b.fill_digest and a.cash == b.cash and a.samples == b.samples
    c = _touch_run(stream, jitter_ns=5_000, seed=4)
    assert c.fill_digest != a.fill_digest


def test_no_lookahead_future_data_cannot_change_past_fills(stream):
    # Same prefix, different future: every fill strictly before the divergence must be identical.
    msgs = list(_split(stream))
    k = len(msgs) // 2
    other = synth.generate(60_000, seed=99, n_stocks=2)
    tail = [m for m in _split(other)][k:]
    alt = b"".join(msgs[:k] + tail)
    cut_ts = _ts(msgs[k])
    a = [x for x in _touch_run(stream).fills if x.ts < cut_ts]
    b = [x for x in _touch_run(alt).fills if x.ts < cut_ts]
    assert [(f.ts, f.order_id, f.price, f.qty) for f in a] == [(f.ts, f.order_id, f.price, f.qty) for f in b]
    assert len(a) > 0


def _split(buf):
    off = 0
    while off < len(buf):
        n = int.from_bytes(buf[off:off + 2], "big")
        yield buf[off:off + 2 + n]
        off += 2 + n


def _ts(frame):
    return int.from_bytes(frame[7:13], "big")


def test_post_only_rejected_when_marketable_on_arrival():
    s = Script([None, None, ("submit", "B", ASK, 100, False, True)])
    bt = run([
        itch.add(1000, L, 1, "B", 100, "T", BID),
        itch.add(1000, L, 2, "S", 100, "T", ASK),
        itch.add(1000, L, 3, "S", 100, "T", ASK + 100),
        itch.add(2000, L, 4, "B", 100, "T", BID - 100),
    ], s)
    assert s.fills == [] and bt.stats["post_only_rejects"] == 1 and bt.open_orders == []
