"""Seeded synthetic ITCH 5.0 market generator (ADR 0004).

Produces a valid, realistic-looking message stream: per-stock random-walk mid, limit adds placed a
geometric number of ticks from the touch (never crossing), partial/full cancels, deletes,
replaces, executions that consume the best level in FIFO order (as an exchange would), hidden
trades ('P'), and the usual system events and stock directory. `fault_rate` injects malformed
traffic (unknown refs, over-fills, duplicate refs, zero-share adds, bad lengths) to test that every
implementation handles it identically.
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

from . import itch

TICK = 100  # $0.01 in ITCH price units (4 implied decimals)
OPEN_NS = 34_200 * 1_000_000_000  # 09:30:00


class _Stock:
    def __init__(self, locate: int, sym: str, mid: int) -> None:
        self.locate = locate
        self.sym = sym
        self.mid = mid
        self.side = {"B": {}, "S": {}}  # price -> [refs] FIFO

    def best(self, s: str):
        lv = self.side[s]
        if not lv:
            return None
        return max(lv) if s == "B" else min(lv)


def generate(n_messages: int, seed: int = 1, n_stocks: int = 8, fault_rate: float = 0.0) -> bytes:
    rng = random.Random(seed)
    out: list[bytes] = []
    ts = OPEN_NS - 1_000_000_000
    out.append(itch.system_event(ts, "O"))
    stocks = []
    for i in range(n_stocks):
        sym = f"SYN{i:02d}"
        mid = rng.randrange(10, 400) * 100 * TICK  # $10-$400 on the tick grid
        stocks.append(_Stock(i + 1, sym, mid))
        out.append(itch.stock_directory(ts, i + 1, sym))
    out.append(itch.system_event(OPEN_NS, "Q"))
    ts = OPEN_NS

    orders: dict[int, list] = {}  # ref -> [stock, side, price, shares]
    next_ref = 1000
    match = 1

    def place(st: _Stock, ts: int, side: str) -> bytes:
        nonlocal next_ref
        other = st.best("S" if side == "B" else "B")
        dist = 0
        while rng.random() < 0.6 and dist < 40:
            dist += 1
        if side == "B":
            price = st.mid - TICK - (dist * TICK // 2) // TICK * TICK
            if other is not None and price >= other:
                price = other - TICK
        else:
            price = st.mid + TICK + (dist * TICK // 2) // TICK * TICK
            if other is not None and price <= other:
                price = other + TICK
        price = max(price, TICK)
        shares = rng.choice((100, 100, 100, 200, 300, 500, 1000)) if rng.random() < 0.9 else rng.randrange(1, 100)
        ref = next_ref
        next_ref += rng.randrange(1, 4)
        orders[ref] = [st, side, price, shares]
        st.side[side].setdefault(price, []).append(ref)
        mpid = "SYNM" if rng.random() < 0.1 else None
        return itch.add(ts, st.locate, ref, side, shares, st.sym, price, mpid)

    def unlink(ref: int) -> None:
        st, side, price, _ = orders.pop(ref)
        q = st.side[side][price]
        q.remove(ref)
        if not q:
            del st.side[side][price]

    while len(out) < n_messages:
        ts += rng.randrange(50, 50_000)
        st = rng.choice(stocks)
        if rng.random() < 0.02:
            st.mid += rng.choice((-TICK, TICK))
            st.mid = max(st.mid, 2 * TICK)
        if fault_rate and rng.random() < fault_rate:
            kind = rng.randrange(5)
            if kind == 0:
                out.append(itch.delete(ts, st.locate, 1))  # ref never issued
            elif kind == 1 and orders:
                ref = rng.choice(list(orders))
                o = orders[ref]
                out.append(itch.cancel(ts, o[0].locate, ref, o[3] + 100))  # over-cancel removes it
                unlink(ref)
            elif kind == 2 and orders:
                ref = rng.choice(list(orders))
                o = orders[ref]
                out.append(itch.add(ts, o[0].locate, ref, o[1], 100, o[0].sym, o[2]))  # duplicate
            elif kind == 3:
                out.append(itch.add(ts, st.locate, next_ref, "B", 0, st.sym, st.mid))  # zero shares
                next_ref += 1
            else:
                out.append(b"\x00\x05Zjunk")  # unknown type
            continue

        n_live = sum(len(q) for s in "BS" for q in st.side[s].values())
        r = rng.random()
        if n_live < 6 or r < 0.48:
            out.append(place(st, ts, rng.choice("BS")))
        elif r < 0.62:  # partial or full cancel of a random live order in this stock
            side = rng.choice("BS")
            if not st.side[side]:
                continue
            price = rng.choice(list(st.side[side]))
            ref = rng.choice(st.side[side][price])
            o = orders[ref]
            if o[3] > 1 and rng.random() < 0.5:
                k = rng.randrange(1, o[3])
                out.append(itch.cancel(ts, st.locate, ref, k))
                o[3] -= k
            else:
                out.append(itch.cancel(ts, st.locate, ref, o[3]))
                unlink(ref)
        elif r < 0.76:
            side = rng.choice("BS")
            if not st.side[side]:
                continue
            price = rng.choice(list(st.side[side]))
            ref = rng.choice(st.side[side][price])
            out.append(itch.delete(ts, st.locate, ref))
            unlink(ref)
        elif r < 0.84:  # replace: new ref, maybe new price/size, loses priority
            side = rng.choice("BS")
            if not st.side[side]:
                continue
            price = rng.choice(list(st.side[side]))
            ref = rng.choice(st.side[side][price])
            o = orders[ref]
            unlink(ref)
            new_price = o[2] + rng.choice((-2, -1, 0, 0, 1, 2)) * TICK
            other = st.best("S" if side == "B" else "B")
            if side == "B" and other is not None and new_price >= other:
                new_price = other - TICK
            if side == "S" and other is not None and new_price <= other:
                new_price = other + TICK
            new_price = max(new_price, TICK)
            new_shares = max(1, o[3] + rng.choice((-100, 0, 0, 100)))
            new_ref = next_ref
            next_ref += 1
            orders[new_ref] = [st, side, new_price, new_shares]
            st.side[side].setdefault(new_price, []).append(new_ref)
            out.append(itch.replace(ts, st.locate, ref, new_ref, new_shares, new_price))
        elif r < 0.98:  # aggressive order consumes the best level FIFO -> executions
            side = rng.choice("BS")  # resting side being hit
            best = st.best(side)
            if best is None:
                continue
            want = rng.choice((100, 100, 200, 300, 500, 800))
            q = st.side[side][best]
            while want > 0 and best in st.side[side]:
                ref = q[0]
                o = orders[ref]
                k = min(want, o[3])
                if rng.random() < 0.15:
                    out.append(itch.executed_price(ts, st.locate, ref, k, match, best + rng.choice((0, TICK))))
                else:
                    out.append(itch.executed(ts, st.locate, ref, k, match))
                match += 1
                want -= k
                o[3] -= k
                if o[3] == 0:
                    unlink(ref)
                ts += rng.randrange(1, 20)
        else:  # non-displayed trade
            out.append(itch.trade(ts, st.locate, 0, rng.choice("BS"), 100, st.sym, st.mid, match))
            match += 1

    out = out[:n_messages]
    return b"".join(out)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Write a synthetic ITCH 5.0 file")
    p.add_argument("out", type=Path)
    p.add_argument("-n", "--messages", type=int, default=100_000)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--stocks", type=int, default=8)
    p.add_argument("--fault-rate", type=float, default=0.0)
    a = p.parse_args(argv)
    a.out.write_bytes(generate(a.messages, a.seed, a.stocks, a.fault_rate))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
