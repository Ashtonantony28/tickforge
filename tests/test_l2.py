"""M3: exact decimals, L2 book bit-exactness (C++ vs Python), Binance sync/gap/check logic."""
from __future__ import annotations

import gzip
import json
import random
from pathlib import Path

import pytest

from tickforge import l2, load_engine

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def test_parse_dec_exact():
    assert l2.parse_dec("73742.83000000") == 7374283000000
    assert l2.parse_dec("0.00000001") == 1
    assert l2.parse_dec("5") == 500000000
    assert l2.parse_dec("-1.5") == -150000000
    assert l2.fmt_dec(l2.parse_dec("123.4500")) == "123.45"
    with pytest.raises(ValueError):
        l2.parse_dec("0.000000001")


def test_fixture_python_matches_committed():
    exp = json.loads((FIX / "synth_l2.expected.json").read_text())
    r = l2.L2Replayer()
    r.feed((FIX / "synth_l2.bin").read_bytes())
    assert f"{r.h:016x}" == exp["digest"]
    assert r.batches == exp["batches"] and r.crossed == exp["crossed"]
    assert [list(x) for x in r.book.top(2, True)] == exp["bids"]
    assert [list(x) for x in r.book.top(2, False)] == exp["asks"]


def test_fixture_regenerates_identically():
    assert l2.encode(l2.synth_events(1000, 5)) == (FIX / "synth_l2.bin").read_bytes()


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_cpp_l2_matches_python(seed):
    eng = _engine()
    data = l2.encode(l2.synth_events(5000, seed))
    py = l2.L2Replayer()
    py.feed(data)
    cpp = eng.L2Replayer()
    assert cpp.feed(data) == len(data)
    assert cpp.digest == py.h
    assert cpp.batches == py.batches and cpp.crossed == py.crossed
    assert cpp.top(10, True) == py.book.top(10, True)
    assert cpp.top(10, False) == py.book.top(10, False)


def _engine():
    try:
        return load_engine()
    except ImportError:
        pytest.skip("C++ engine not built")


# ---- synthetic Binance recording --------------------------------------------------------------

def _fmt(v: int) -> str:
    return l2.fmt_dec(v) if v else "0.00000000"


class FakeVenue:
    """Generates depth diffs with contiguous update ids, and REST snapshots of the true book."""

    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.book = l2.L2Book()
        self.uid = 1_000_000
        self.t = 0
        for i in range(30):
            self.book.set(True, (100_00 - i) * 10**6, 10**8)
            self.book.set(False, (100_01 + i) * 10**6, 10**8)

    def diff(self) -> dict:
        n_ids = self.rng.randint(1, 6)
        b, a = [], []
        for _ in range(self.rng.randint(1, 5)):
            is_bid = self.rng.random() < 0.5
            off = self.rng.randint(0, 25)
            px = (100_00 - off if is_bid else 100_01 + off) * 10**6
            q = 0 if self.rng.random() < 0.25 else self.rng.randint(1, 900) * 10**6
            self.book.set(is_bid, px, q)
            (b if is_bid else a).append([_fmt(px), _fmt(q)])
        U = self.uid + 1
        self.uid += n_ids
        self.t += 100_000_000
        return {"e": "depthUpdate", "U": U, "u": self.uid, "b": b, "a": a}

    def snapshot(self, limit: int = 100) -> dict:
        return {"lastUpdateId": self.uid,
                "bids": [[_fmt(p), _fmt(q)] for p, q in self.book.top(limit, True)],
                "asks": [[_fmt(p), _fmt(q)] for p, q in self.book.top(limit, False)]}

    def rec(self, k, d):
        return {"t": self.t, "k": k, "d": d}


def _recording(seed=0, n=3000, snap_every=200, late=3, drop_at=None, limit=100):
    """Snapshots are taken at one point and delivered `late` diffs later, like a slow REST call."""
    v = FakeVenue(seed)
    out, pending = [v.rec("meta", {"event": "connect"})], []
    for _ in range(3):
        out.append(v.rec("depth", v.diff()))  # diffs arrive before the first snapshot
    pending.append((late, v.snapshot(limit)))
    for i in range(n):
        d = v.diff()
        if drop_at is not None and i == drop_at:
            continue  # lost message -> gap
        out.append(v.rec("depth", d))
        if i % snap_every == snap_every - 1:
            pending.append((late, v.snapshot(limit)))
        nxt = []
        for k, s in pending:
            (out.append(v.rec("snap", s)) if k == 0 else nxt.append((k - 1, s)))
        pending = nxt
    for _, s in pending:
        out.append(v.rec("snap", s))
    return out, v


def test_sync_clean_recording_checks_pass():
    recs, venue = _recording(seed=1)
    s = l2.BinanceSync()
    for r in recs:
        s.process(r)
    st = s.stats
    assert st.gaps == 0 and st.check_mismatches == 0
    assert st.checks >= 14 and st.syncs == 1
    # Final local book equals the venue's true book.
    assert s.book.bids == venue.book.bids and s.book.asks == venue.book.asks
    # The emitted event stream replays to the same book.
    r = l2.L2Replayer()
    r.feed(l2.encode(s.out))
    assert r.book.bids == venue.book.bids and r.book.asks == venue.book.asks


def test_sync_detects_gap_and_resyncs():
    recs, venue = _recording(seed=2, drop_at=1000)
    s = l2.BinanceSync()
    for r in recs:
        s.process(r)
    assert s.stats.gaps == 1 and s.stats.syncs == 2
    assert s.stats.check_mismatches == 0
    assert s.book.bids == venue.book.bids and s.book.asks == venue.book.asks


def test_check_catches_corrupted_book():
    recs, _ = _recording(seed=3)
    s = l2.BinanceSync()
    for i, r in enumerate(recs):
        s.process(r)
        if i == 1500:  # phantom level inside the band at an off-grid price no diff ever touches
            s.book.bids[9_999_500_000] = 1
    assert s.stats.check_mismatches >= 1


def test_shallow_snapshots_only_compare_inside_known_depth():
    # Snapshot depth 20 < venue depth 30: deep levels the local book never saw must not be flagged.
    recs, venue = _recording(seed=5, limit=20)
    s = l2.BinanceSync()
    for r in recs:
        s.process(r)
    assert s.stats.check_mismatches == 0 and s.stats.checks >= 14
    for is_bid in (True, False):
        assert s.book.top(10, is_bid) == venue.book.top(10, is_bid)


def test_read_records_tolerates_truncated_tail(tmp_path):
    p = tmp_path / "x.jsonl.gz"
    with gzip.open(p, "wt") as f:
        f.write('{"t":1,"k":"meta","d":{}}\n{"t":2,"k":"me')
    assert [r["t"] for r in l2.read_records([p])] == [1]


def test_check_cli_on_recording(tmp_path):
    recs, _ = _recording(seed=4, n=1000)
    p = tmp_path / "BTCUSDT-x.jsonl.gz"
    with gzip.open(p, "wt") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    ev = tmp_path / "ev.bin"
    assert l2._main(["check", str(tmp_path), "--events", str(ev)]) == 0
    r = l2.L2Replayer()
    assert r.feed(ev.read_bytes()) == ev.stat().st_size


def test_reanchor_extends_checked_band_after_price_drift():
    # The first snapshot is shallow; a later, deeper passing check re-anchors the book so that a
    # wrong level far below the first snapshot's band is still caught (audit finding M9-2).
    def snap(L, bids, asks):
        return {"t": L, "k": "snap", "d": {"lastUpdateId": L, "bids": bids, "asks": asks}}
    s = l2.BinanceSync()
    s.process({"t": 0, "k": "depth", "d": {"U": 9, "u": 10, "b": [], "a": []}})
    s.process(snap(10, [["100.00", "1"], ["99.99", "1"]], [["100.01", "1"]]))
    s.process({"t": 1, "k": "depth", "d": {"U": 11, "u": 11, "b": [["98.00", "2"]], "a": []}})
    s.process(snap(11, [["100.00", "1"], ["99.99", "1"], ["98.00", "2"]], [["100.01", "1"]]))
    assert s.stats.check_mismatches == 0 and s.stats.reanchors == 1
    s.process({"t": 2, "k": "depth", "d": {"U": 12, "u": 12, "b": [["99.00", "5"]], "a": []}})
    s.process(snap(12, [["100.00", "1"], ["99.99", "1"], ["99.00", "7"], ["98.00", "2"]], [["100.01", "1"]]))
    assert s.stats.check_mismatches == 1
    # The emitted event stream still replays to the same book.
    r = l2.L2Replayer()
    r.feed(l2.encode(s.out))
    assert r.book.bids == s.book.bids and r.book.asks == s.book.asks
