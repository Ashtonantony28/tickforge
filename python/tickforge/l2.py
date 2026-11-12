"""L2 crypto data path (M3, ADR 0008): exact decimals, reference L2 book, Binance diff-depth sync.

Pipeline:  recorder.py (raw JSONL.gz)  ->  BinanceSync (this file)  ->  32-byte L2Event stream
           ->  C++ tf::L2Replayer / Python L2Replayer (bit-exact digest).

BinanceSync implements Binance's documented local-book procedure: buffer diffs, take a REST
snapshot with lastUpdateId L, drop diffs with u <= L, require the first applied diff to straddle
L+1, then require U == previous u + 1 for every later diff. Any break is a *gap*: the book is
marked unsynced until the next snapshot. Every later snapshot is also used as a *check*: the book
is rewound (undo log) to update id L and compared level-by-level with the snapshot.
"""
from __future__ import annotations

import gzip
import json
import struct
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

MASK = (1 << 64) - 1
DIGEST_INIT = 0xCBF29CE484222325
DIGEST_PRIME = 0x100000001B3
SCALE_DIGITS = 8
SCALE = 10**SCALE_DIGITS

CLEAR, BID, ASK, BATCH = 0, 1, 2, 3
EVENT = struct.Struct("<B7xqqQ")  # matches tf::L2Event


def parse_dec(s: str) -> int:
    """Exact decimal string -> int at 1e-8 scale. Rejects anything with more than 8 decimals."""
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    whole, _, frac = s.partition(".")
    frac = frac.rstrip("0")
    if len(frac) > SCALE_DIGITS:
        raise ValueError(f"too many decimals for 1e-8 scale: {s!r}")
    v = int(whole or "0") * SCALE + int((frac or "0").ljust(SCALE_DIGITS, "0"))
    return -v if neg else v


def fmt_dec(v: int) -> str:
    sign = "-" if v < 0 else ""
    v = abs(v)
    return f"{sign}{v // SCALE}.{v % SCALE:08d}".rstrip("0").rstrip(".")


class L2Book:
    """Reference L2 book: {price: qty} per side. Obvious, slow, the oracle for the C++ book."""

    def __init__(self) -> None:
        self.bids: dict[int, int] = {}
        self.asks: dict[int, int] = {}

    def side(self, is_bid: bool) -> dict[int, int]:
        return self.bids if is_bid else self.asks

    def set(self, is_bid: bool, price: int, qty: int) -> int:
        s = self.side(is_bid)
        prev = s.get(price, 0)
        if qty == 0:
            s.pop(price, None)
        else:
            s[price] = qty
        return prev

    def clear(self) -> None:
        self.bids.clear()
        self.asks.clear()

    def best(self, is_bid: bool) -> tuple[int, int] | None:
        s = self.side(is_bid)
        if not s:
            return None
        p = max(s) if is_bid else min(s)
        return p, s[p]

    def top(self, n: int, is_bid: bool) -> list[tuple[int, int]]:
        s = self.side(is_bid)
        return sorted(s.items(), reverse=is_bid)[:n]

    def crossed(self) -> bool:
        b, a = self.best(True), self.best(False)
        return b is not None and a is not None and b[0] >= a[0]


class L2Replayer:
    """Python twin of tf::L2Replayer: same events, same digest."""

    def __init__(self) -> None:
        self.book = L2Book()
        self.h = DIGEST_INIT
        self.events = self.batches = self.crossed = self.bad = 0

    def _word(self, w: int) -> None:
        self.h = ((self.h ^ (w & MASK)) * DIGEST_PRIME) & MASK

    def apply(self, kind: int, price: int, qty: int, seq: int) -> None:
        self.events += 1
        if kind == CLEAR:
            self.book.clear()
        elif kind in (BID, ASK):
            self.book.set(kind == BID, price, qty)
        elif kind == BATCH:
            self.batches += 1
            self.crossed += self.book.crossed()
            self._word(seq)
            for is_bid in (True, False):
                b = self.book.best(is_bid)
                self._word(b[0] if b else 0)
                self._word(b[1] if b else 0)
        else:
            self.bad += 1

    def feed(self, data: bytes) -> int:
        used = len(data) - len(data) % EVENT.size
        for kind, price, qty, seq in EVENT.iter_unpack(data[:used]):
            self.apply(kind, price, qty, seq)
        return used


def encode(events: Iterable[tuple[int, int, int, int]]) -> bytes:
    return b"".join(EVENT.pack(*e) for e in events)


# ---------------------------------------------------------------------------------------------
# Raw recordings
# ---------------------------------------------------------------------------------------------

def read_records(paths: Iterable[Path]) -> Iterator[dict]:
    """Yield recorder lines ({"t": recv_ns, "k": kind, "d": payload}) from .jsonl[.gz] files."""
    for p in sorted(Path(x) for x in paths):
        opener = gzip.open if p.suffix == ".gz" else open
        try:
            with opener(p, "rt") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            # A file cut mid-write (crash, kill) ends in a partial line; stop there.
                            break
        except EOFError:
            continue  # truncated gzip tail: keep what was readable


@dataclass
class SyncStats:
    diffs: int = 0
    applied: int = 0
    dropped_stale: int = 0
    snapshots: int = 0
    syncs: int = 0
    gaps: int = 0
    reconnects: int = 0
    checks: int = 0
    checks_exact: int = 0
    check_mismatches: int = 0
    checks_skipped: int = 0
    levels_compared: int = 0
    crossed_batches: int = 0
    trades: int = 0
    first_t: int | None = None
    last_t: int | None = None
    gap_log: list = field(default_factory=list)
    mismatch_log: list = field(default_factory=list)

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if not k.endswith("_log")}
        d["gap_log"] = self.gap_log[:20]
        d["mismatch_log"] = self.mismatch_log[:20]
        if self.first_t is not None and self.last_t is not None:
            d["hours"] = round((self.last_t - self.first_t) / 3.6e12, 3)
        return d


class BinanceSync:
    """Turns a raw Binance recording (depth diffs + REST snapshots) into synced L2 events.

    `undo_depth` bounds how far back a late snapshot can be checked (diffs arrive every 100 ms, so
    the default covers ~20 s of REST latency).
    """

    def __init__(self, undo_depth: int = 200) -> None:
        self.book = L2Book()
        self.synced = False
        self.last_u: int | None = None
        self.pending: list[dict] = []  # diffs buffered while unsynced
        self.undo: deque = deque(maxlen=undo_depth)  # (U, u, [(is_bid, price, prev_qty, new_qty)])
        self.stats = SyncStats()
        self.out: list[tuple[int, int, int, int]] = []
        self.horizon: dict[bool, int | None] = {}

    # -- helpers -----------------------------------------------------------------------------
    def _apply_diff(self, d: dict) -> None:
        changes = []
        for is_bid, key, kind in ((True, "b", BID), (False, "a", ASK)):
            for px_s, q_s in d[key]:
                px, q = parse_dec(px_s), parse_dec(q_s)
                changes.append((is_bid, px, self.book.set(is_bid, px, q), q))
                self.out.append((kind, px, q, 0))
        self.out.append((BATCH, 0, 0, d["u"]))
        self.undo.append((d["U"], d["u"], changes))
        self.last_u = d["u"]
        self.stats.applied += 1
        self.stats.crossed_batches += self.book.crossed()

    def _load_snapshot(self, snap: dict) -> None:
        self.book.clear()
        self.out.append((CLEAR, 0, 0, 0))
        for is_bid, key, kind in ((True, "bids", BID), (False, "asks", ASK)):
            for px_s, q_s in snap[key]:
                px, q = parse_dec(px_s), parse_dec(q_s)
                self.book.set(is_bid, px, q)
                self.out.append((kind, px, q, 0))
        L = snap["lastUpdateId"]
        self.out.append((BATCH, 0, 0, L))
        # Depth horizon: levels beyond the loaded snapshot's depth are unknown until a diff touches
        # them, so later checks only compare prices inside this band.
        self.horizon = {True: min(self.book.bids, default=None), False: max(self.book.asks, default=None)}
        self.undo.clear()
        self.last_u = L
        self.synced = True
        self.stats.syncs += 1

    def _try_sync(self, snap: dict) -> None:
        L = snap["lastUpdateId"]
        keep = [d for d in self.pending if d["u"] > L]
        self.stats.dropped_stale += len(self.pending) - len(keep)
        if keep and keep[0]["U"] > L + 1:
            # Snapshot is older than the oldest buffered diff we still hold: wait for a newer one.
            self.pending = keep
            return
        self.pending = []
        self._load_snapshot(snap)
        for d in keep:
            self._on_diff(d)

    def _gap(self, t: int, why: str) -> None:
        self.stats.gaps += 1
        self.stats.gap_log.append({"t": t, "why": why, "last_u": self.last_u})
        self.synced = False
        self.pending = []

    def _check(self, t: int, snap: dict) -> None:
        """Rewind to snapshot id L, compare inside the snapshot's price band, replay forward."""
        L = snap["lastUpdateId"]
        if self.last_u is None or L > self.last_u:
            # Snapshot newer than anything applied: compare later is not possible without
            # buffering; count it (rare: REST faster than the stream) and move on.
            self.stats.checks_skipped += 1
            return
        undone = []
        while self.undo and self.undo[-1][0] > L:  # entries entirely after L
            U, u, ch = self.undo.pop()
            for is_bid, px, prev, _new in reversed(ch):
                self.book.set(is_bid, px, prev)
            undone.append((U, u, ch))
        if not self.undo and not (undone and undone[-1][0] == L + 1):
            # Rewound to the undo horizon without reaching id L exactly: cannot check this one.
            self.stats.checks_skipped += 1
            self._redo(undone)
            return
        # State now = after the last diff with U <= L. If that diff ends past L, it straddles L and
        # its prices are excluded (the snapshot saw only part of it).
        exclude: set[tuple[bool, int]] = set()
        if self.undo and self.undo[-1][1] > L:
            exclude = {(b, p) for b, p, _, _ in self.undo[-1][2]}
        exact = not exclude
        self.stats.checks += 1
        self.stats.checks_exact += exact
        bad = self._compare(snap, exclude)
        if bad:
            self.stats.check_mismatches += 1
            self.stats.mismatch_log.append({"t": t, "L": L, "examples": bad[:5]})
        self._redo(undone)

    def _redo(self, undone: list) -> None:
        for U, u, ch in reversed(undone):
            for is_bid, px, _prev, new in ch:
                self.book.set(is_bid, px, new)
            self.undo.append((U, u, ch))

    def _compare(self, snap: dict, exclude: set) -> list:
        bad = []
        for is_bid, key in ((True, "bids"), (False, "asks")):
            lv = {parse_dec(p): parse_dec(q) for p, q in snap[key]}
            if not lv:
                continue
            edge = min(lv) if is_bid else max(lv)  # snapshot depth limit: only compare inside it
            h = self.horizon.get(is_bid)
            if h is not None:
                edge = max(edge, h) if is_bid else min(edge, h)
            mine = {p: q for p, q in self.book.side(is_bid).items()
                    if (p >= edge if is_bid else p <= edge)}
            inside = (lambda x: x >= edge) if is_bid else (lambda x: x <= edge)
            for p in {x for x in lv if inside(x)} | set(mine):
                if (is_bid, p) in exclude:
                    continue
                self.stats.levels_compared += 1
                if lv.get(p, 0) != mine.get(p, 0):
                    bad.append({"side": key, "price": fmt_dec(p), "snap": fmt_dec(lv.get(p, 0)),
                                "book": fmt_dec(mine.get(p, 0))})
        return bad

    def _on_diff(self, d: dict, t: int = -1) -> None:
        if not self.synced:
            self.pending.append(d)
            return
        if d["u"] <= self.last_u:
            self.stats.dropped_stale += 1
            return
        if d["U"] > self.last_u + 1:
            self._gap(t, f"U={d['U']} after u={self.last_u}")
            self.pending.append(d)
            return
        self._apply_diff(d)

    # -- driver ------------------------------------------------------------------------------
    def process(self, rec: dict) -> None:
        t, k, d = rec["t"], rec["k"], rec.get("d")
        st = self.stats
        st.first_t = t if st.first_t is None else st.first_t
        st.last_t = t
        if k == "depth":
            st.diffs += 1
            self._on_diff(d, t)
        elif k == "snap":
            st.snapshots += 1
            if self.synced:
                self._check(t, d)
            else:
                self._try_sync(d)
        elif k == "trade":
            st.trades += 1
        elif k == "meta" and d.get("event") == "connect" and st.diffs:
            # Reconnects are counted, not treated as gaps: with redundant connections the stream
            # can be continuous across one, and the U == u + 1 check catches any real loss.
            st.reconnects += 1



# ---------------------------------------------------------------------------------------------
# Synthetic L2 stream (committed fixture generator; seeded, no wall clock)
# ---------------------------------------------------------------------------------------------

def synth_events(n_batches: int, seed: int, levels: int = 40, tick: int = SCALE // 100) -> list:
    """Random walk L2 stream with clears, deletes, far-level noise and occasional crossed batches."""
    import random

    rng = random.Random(seed)
    mid = 50_000 * SCALE
    ev: list[tuple[int, int, int, int]] = [(CLEAR, 0, 0, 0)]
    seq = 1000
    book = L2Book()  # tracked so stale levels behind a moving mid get deleted, like a real venue
    for i in range(n_batches):
        if i and rng.random() < 0.002:
            ev.append((CLEAR, 0, 0, 0))
            book.clear()
        mid += rng.choice((-1, 0, 0, 1)) * tick
        for _ in range(rng.randint(1, 12)):
            is_bid = rng.random() < 0.5
            # ~3% of updates land one tick through the mid (transiently crossed batches)
            off = int(rng.expovariate(0.15)) % levels + (0 if rng.random() < 0.97 else -1)
            px = mid - (off + 1) * tick if is_bid else mid + off * tick
            qty = 0 if rng.random() < 0.3 else rng.randint(1, 5_000_000) * 1000
            ev.append((BID if is_bid else ASK, px, qty, 0))
            book.set(is_bid, px, qty)
        if rng.random() < 0.9:  # usually clean up levels the mid walked past
            for p in [p for p in book.bids if p >= mid]:
                ev.append((BID, p, 0, 0))
                book.set(True, p, 0)
            for p in [p for p in book.asks if p < mid]:
                ev.append((ASK, p, 0, 0))
                book.set(False, p, 0)
        seq += rng.randint(1, 9)
        ev.append((BATCH, 0, 0, seq))
    return ev


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    p = argparse.ArgumentParser(prog="python -m tickforge.l2")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="sync a raw recording, verify vs snapshots, emit events")
    c.add_argument("paths", nargs="+", type=Path, help="recording files or directories")
    c.add_argument("--events", type=Path, help="write the synced 32-byte event stream here")
    c.add_argument("--require-hours", type=float, default=0.0)
    s = sub.add_parser("synth", help="write a synthetic event stream")
    s.add_argument("out", type=Path)
    s.add_argument("-n", type=int, default=20000)
    s.add_argument("--seed", type=int, default=5)
    a = p.parse_args(argv)

    if a.cmd == "synth":
        a.out.write_bytes(encode(synth_events(a.n, a.seed)))
        return 0
    files = []
    for x in a.paths:
        files += sorted(x.glob("*.jsonl*")) if x.is_dir() else [x]
    sync = BinanceSync()
    out = a.events.open("wb") if a.events else None
    for rec in read_records(files):
        sync.process(rec)
        if out and len(sync.out) > 65536:
            out.write(encode(sync.out))
            sync.out.clear()
    if out:
        out.write(encode(sync.out))
        out.close()
    st = sync.stats.as_dict()
    st["files"] = len(files)
    ok = (st["gaps"] == 0 and st["check_mismatches"] == 0 and st["checks"] > 0
          and st.get("hours", 0) >= a.require_hours)
    st["pass"] = ok
    print(json.dumps(st, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
