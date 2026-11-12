"""Bit-exact comparison of the C++ engine against the Python reference book (CLAUDE.md directive 2).

Covers the committed fixtures plus freshly generated streams across seeds, with and without
injected faults, and checks digest, stats, every book's top-5 depth, and FIFO queues.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tickforge import load_engine, refbook, synth

ROOT = Path(__file__).resolve().parents[1]
eng = load_engine()


def _compare(buf: bytes, check_queues: bool = True) -> None:
    ref = refbook.RefReplayer()
    ref.feed(buf)
    cpp = eng.Replayer()
    assert cpp.feed(buf) == len(buf)
    assert cpp.messages == ref.messages
    assert cpp.digest == ref.digest, f"{cpp.digest:016x} != {ref.digest:016x}"
    assert cpp.stats == ref.books.stats
    assert cpp.live_orders == len(ref.books.orders)
    assert cpp.bad_messages == ref.bad
    assert cpp.trades == ref.trades
    assert cpp.symbols == ref.symbols
    for loc in ref.books.levels:
        for side in "BS":
            assert cpp.depth(loc, side, 5) == ref.books.depth(loc, side, 5)
            if check_queues:
                for price, _, _ in ref.books.depth(loc, side, 3):
                    assert cpp.queue(loc, side, price) == ref.books.queue(loc, side, price)


@pytest.mark.parametrize("stem", ["synth_small", "synth_faults"])
def test_committed_fixture(stem):
    buf = (ROOT / "fixtures" / f"{stem}.itch").read_bytes()
    exp = json.loads((ROOT / "fixtures" / f"{stem}.expected.json").read_text())
    cpp = eng.Replayer()
    cpp.feed(buf)
    assert f"{cpp.digest:016x}" == exp["digest"]
    for loc, sides in exp["books"].items():
        for side, levels in sides.items():
            assert [list(x) for x in cpp.depth(int(loc), side, exp["depth"])] == levels
    _compare(buf)


@pytest.mark.parametrize("seed", range(1, 9))
def test_random_streams(seed):
    _compare(synth.generate(15_000, seed=seed * 101, n_stocks=1 + seed % 5))


@pytest.mark.parametrize("seed", range(1, 5))
def test_random_streams_with_faults(seed):
    _compare(synth.generate(10_000, seed=seed * 7, n_stocks=2, fault_rate=0.05))


def test_chunked_feed_matches_whole():
    buf = synth.generate(5_000, seed=3)
    whole = eng.Replayer()
    whole.feed(buf)
    part = eng.Replayer()
    rest = b""
    for i in range(0, len(buf), 777):
        data = rest + buf[i:i + 777]
        used = part.feed(data)
        rest = data[used:]
    assert rest == b""
    assert part.digest == whole.digest
    assert part.messages == whole.messages


def test_generator_is_deterministic():
    assert synth.generate(3_000, seed=5) == synth.generate(3_000, seed=5)
    assert synth.generate(3_000, seed=5) != synth.generate(3_000, seed=6)
