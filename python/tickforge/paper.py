"""M6 live paper trading on the public Binance feed (ADR 0011). Simulated fills only.

    python -m tickforge.paper run --hours 4 --out data/raw/paper/run1     # live
    python -m tickforge.paper replay data/raw/paper/run1                  # same code, recorded feed
    python -m tickforge.paper verify data/raw/paper/run1                  # log hash chain + replay match

Design: the daemon is record-driven. Every websocket/REST payload is first written to the raw
recording (same format as tickforge.recorder) and then handed to `PaperEngine.process`, which uses
only the record's own timestamp, never the wall clock. Replaying the recording through the same
engine must therefore reproduce the live decision log byte-for-byte; `verify` checks that, plus
the SHA-256 hash chain linking every log line to the previous one.

There is no order-entry code anywhere: quotes and fills exist only in this process
(CLAUDE.md rule 4). Market data comes from the keyless `*.binance.vision` hosts.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .l2 import SCALE, BinanceSync, parse_dec, read_records


@dataclass
class PaperParams:
    gamma: float = 0.001   # risk aversion per lot (USDT^-1); inventory term = q_lots * gamma * sigma^2 * tau
    k: float = 5.0         # fill-intensity decay per USDT of distance (half-spread ~ 1/k for small gamma)
    tau_s: float = 60.0
    size: int = 100_000    # 0.001 BTC at 1e-8
    max_pos: int = 1_000_000  # 0.01 BTC
    tick: int = 1_000_000  # 0.01 USDT at 1e-8
    alpha_ticks: float = 0.0
    maker_fee_bps: float = 0.0  # Binance spot VIP0 maker is 10 bps; 0 isolates the strategy, fee shown separately
    requote_ms: int = 200
    latency_ms: int = 50


class Order:
    __slots__ = ("side", "px", "qty", "ahead", "live_at")

    def __init__(self, side, px, qty, ahead, live_at):
        self.side, self.px, self.qty, self.ahead, self.live_at = side, px, qty, ahead, live_at


class PaperEngine:
    """A-S quoting on the synced L2 book with an L2 queue-position fill model.

    Fill model (L2 has no order ids, so it is conservative): a resting order's `ahead` starts at
    the level's displayed quantity when it goes live (after `latency_ms`). Trades at our price
    consume `ahead` first, then fill us. A trade through our price, or the opposite touch reaching
    it, fills us in full. Level decreases without trades (cancels) reduce `ahead` only down to the
    new level size, i.e. we assume cancels come from behind us.
    """

    def __init__(self, p: PaperParams, log_path: Path | None = None):
        self.p = p
        self.sync = BinanceSync()
        self.orders: dict[str, Order | None] = {"B": None, "S": None}
        self.pos = 0
        self.cash_usdt = 0.0
        self.fees_usdt = 0.0
        self.fills = 0
        self.last_quote_t = {"B": 0, "S": 0}
        self.sigma2 = 0.0
        self.last_sec = None
        self.last_mid = None
        self.next_mark = None
        self.log_lines: list[str] = []
        self.prev_hash = "0" * 64
        self.log_f = open(log_path, "w") if log_path else None

    # -- logging -----------------------------------------------------------------------------
    def log(self, t: int, ev: str, **kw) -> None:
        body = json.dumps({"t": t, "ev": ev, **kw}, separators=(",", ":"), sort_keys=True)
        h = hashlib.sha256((self.prev_hash + body).encode()).hexdigest()
        line = json.dumps({"h": h, "prev": self.prev_hash, "b": body}, separators=(",", ":"))
        self.prev_hash = h
        self.log_lines.append(line)
        if self.log_f:
            self.log_f.write(line + "\n")
            self.log_f.flush()

    # -- market --------------------------------------------------------------------------------
    def best(self):
        b, a = self.sync.book.best(True), self.sync.book.best(False)
        return b, a

    def mid_usdt(self):
        b, a = self.best()
        return (b[0] + a[0]) / 2 / SCALE if b and a else None

    def process(self, rec: dict) -> None:
        t, k = rec["t"], rec["k"]
        self.sync.process(rec)
        if not self.sync.synced:
            if any(self.orders.values()):
                self.orders = {"B": None, "S": None}
                self.log(t, "pull", why="unsynced")
            return
        self._activate(t)
        if k == "trade":
            self.on_trade(t, rec["d"])
        elif k == "depth":
            self.on_depth(t)
        self.mark(t)
        self.quote(t)

    def _activate(self, t):
        for side, o in self.orders.items():
            if o is not None and o.ahead is None and t >= o.live_at:
                book = self.sync.book.bids if side == "B" else self.sync.book.asks
                o.ahead = book.get(o.px, 0)

    def on_trade(self, t, d):
        px, q = parse_dec(d["p"]), parse_dec(d["q"])
        for side, o in self.orders.items():
            if o is None or o.ahead is None:
                continue
            # Buyer-maker trade = a sell aggressor hit bids; it can fill our bid at px <= our price.
            hits_us = (side == "B" and d["m"] and px <= o.px) or (side == "S" and not d["m"] and px >= o.px)
            if not hits_us:
                continue
            if px != o.px:  # traded through us
                self.fill(t, side, o.qty, "through")
                continue
            take = min(q, o.ahead)
            o.ahead -= take
            rem = q - take
            if rem > 0:
                self.fill(t, side, min(rem, o.qty), "queue")

    def on_depth(self, t):
        b, a = self.best()
        for side, o in list(self.orders.items()):
            if o is None or o.ahead is None:
                continue
            book = self.sync.book.bids if side == "B" else self.sync.book.asks
            lvl = book.get(o.px, 0)
            o.ahead = min(o.ahead, lvl)
            opp = a if side == "B" else b
            if opp and ((side == "B" and opp[0] <= o.px) or (side == "S" and opp[0] >= o.px)):
                self.fill(t, side, o.qty, "cross")

    def fill(self, t, side, qty, why):
        o = self.orders[side]
        if o is None or qty <= 0:
            return
        qty = min(qty, o.qty)
        sgn = 1 if side == "B" else -1
        notional = o.px / SCALE * qty / SCALE
        self.pos += sgn * qty
        self.cash_usdt -= sgn * notional
        fee = notional * self.p.maker_fee_bps / 1e4
        self.fees_usdt += fee
        self.cash_usdt -= fee
        o.qty -= qty
        self.fills += 1
        mid = self.mid_usdt()
        self.log(t, "fill", side=side, px=o.px, qty=qty, why=why, pos=self.pos, mid=round(mid, 4) if mid else None)
        if o.qty == 0:
            self.orders[side] = None

    def equity(self):
        mid = self.mid_usdt()
        return self.cash_usdt + (self.pos / SCALE) * mid if mid else None

    def mark(self, t):
        mid = self.mid_usdt()
        if mid is None:
            return
        sec = t // 1_000_000_000
        if self.last_sec is not None and sec > self.last_sec:
            dt = sec - self.last_sec
            a = 1 - 2 ** (-dt / 60)
            obs = (mid - self.last_mid) ** 2 / dt
            self.sigma2 = (1 - a) * self.sigma2 + a * obs if self.sigma2 else obs
        if self.last_sec is None or sec > self.last_sec:
            self.last_sec, self.last_mid = sec, mid
        if self.next_mark is None:
            self.next_mark = (t // 60_000_000_000 + 1) * 60_000_000_000
        if t >= self.next_mark:
            self.log(t, "mark", pos=self.pos, equity=round(self.equity(), 6), fees=round(self.fees_usdt, 6),
                     fills=self.fills, mid=round(mid, 4), sigma=round(math.sqrt(self.sigma2), 6))
            self.next_mark += 60_000_000_000

    def quote(self, t):
        b, a = self.best()
        if not b or not a or a[0] <= b[0]:
            return
        p = self.p
        mid = (b[0] + a[0]) / 2 / SCALE
        imb = (b[1] - a[1]) / (b[1] + a[1])
        q = self.pos / p.size  # inventory in lots
        tick = p.tick / SCALE
        var_tau = self.sigma2 * p.tau_s
        r = mid + p.alpha_ticks * imb * tick - q * p.gamma * var_tau
        delta = 0.5 * p.gamma * var_tau + math.log1p(p.gamma / p.k) / p.gamma
        want = {"B": min(int(math.floor((r - delta) / tick)) * p.tick, b[0]),
                "S": max(int(math.ceil((r + delta) / tick)) * p.tick, a[0])}
        for side in ("B", "S"):
            o = self.orders[side]
            if o is not None and o.px == want[side]:
                continue
            if t - self.last_quote_t[side] < p.requote_ms * 1_000_000:
                continue
            sgn = 1 if side == "B" else -1
            if sgn * self.pos + p.size > p.max_pos:
                if o is not None:
                    self.orders[side] = None
                    self.log(t, "cancel", side=side, why="limit")
                continue
            book = self.sync.book.bids if side == "B" else self.sync.book.asks
            # `ahead` is unknown until the order reaches the venue: it is taken from the book at the first
            # event after live_at (see _activate), so size added during the latency queues ahead of us.
            self.orders[side] = Order(side, want[side], p.size, None, t + p.latency_ms * 1_000_000)
            self.last_quote_t[side] = t
            self.log(t, "quote", side=side, px=want[side])

    def summary(self) -> dict:
        return {"fills": self.fills, "position": self.pos, "equity_usdt": self.equity(), "fees_usdt": self.fees_usdt,
                "log_lines": len(self.log_lines), "last_hash": self.prev_hash, "sync": self.sync.stats.as_dict()}


# ---- live daemon -------------------------------------------------------------------------------

async def live(out: Path, hours: float, p: PaperParams, symbol: str = "BTCUSDT") -> dict:
    from .recorder import Recorder

    out.mkdir(parents=True, exist_ok=True)
    eng = PaperEngine(p, out / "paper.log")
    rec = Recorder(symbol, out / "feed", hours, snapshot_every=300, limit=1000)
    orig = rec.w.write

    def tee(t_ns, kind, payload):  # every record goes to disk first, then to the engine
        orig(t_ns, kind, payload)
        eng.process({"t": t_ns, "k": kind, "d": payload})

    rec.w.write = tee
    stats = await rec.run()
    s = eng.summary()
    s["recorder"] = stats
    (out / "summary.json").write_text(json.dumps(s, indent=2))
    return s


def replay(run_dir: Path, p: PaperParams) -> PaperEngine:
    eng = PaperEngine(p)
    for r in read_records(sorted((run_dir / "feed").glob("*.jsonl*"))):
        eng.process(r)
    return eng


def verify(run_dir: Path, p: PaperParams) -> dict:
    lines = (run_dir / "paper.log").read_text().splitlines()
    prev, chain_ok = "0" * 64, True
    for ln in lines:
        e = json.loads(ln)
        if e["prev"] != prev or hashlib.sha256((prev + e["b"]).encode()).hexdigest() != e["h"]:
            chain_ok = False
            break
        prev = e["h"]
    eng = replay(run_dir, p)
    n = min(len(lines), len(eng.log_lines))
    match = lines[:n] == eng.log_lines[:n]
    first_diff = next((i for i in range(n) if lines[i] != eng.log_lines[i]), None)
    return {"log_lines": len(lines), "replay_lines": len(eng.log_lines), "hash_chain_ok": chain_ok,
            "replay_prefix_identical": match, "first_diff": first_diff,
            # A live run killed mid-write can end a few records before its recording does.
            "replay_extra_lines": len(eng.log_lines) - len(lines), "summary": eng.summary()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tickforge.paper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--hours", type=float, default=1.0)
    r.add_argument("--alpha-ticks", type=float, default=0.0)
    for name in ("replay", "verify"):
        s = sub.add_parser(name)
        s.add_argument("run_dir", type=Path)
        s.add_argument("--alpha-ticks", type=float, default=0.0)
    a = ap.parse_args(argv)
    p = PaperParams(alpha_ticks=a.alpha_ticks)
    if a.cmd == "run":
        print(json.dumps(asyncio.run(live(a.out, a.hours, p)), indent=2, default=str))
    elif a.cmd == "replay":
        print(json.dumps(replay(a.run_dir, p).summary(), indent=2, default=str))
    else:
        v = verify(a.run_dir, p)
        print(json.dumps(v, indent=2, default=str))
        return 0 if v["hash_chain_ok"] and v["replay_prefix_identical"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
