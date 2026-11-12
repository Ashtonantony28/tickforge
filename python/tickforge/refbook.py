"""Slow, obviously-correct reference order book (the Python side of the bit-exact contract).

Semantics mirror engine/include/tickforge/{order_book,replay}.hpp, including the digest and the
handling of malformed input (unknown refs, over-fills clamped, zero-share adds ignored).
"""
from __future__ import annotations

from collections import defaultdict

from .itch import decode

MASK = (1 << 64) - 1
DIGEST_INIT = 0xCBF29CE484222325
DIGEST_PRIME = 0x100000001B3

STAT_KEYS = ("adds", "executions", "cancels", "deletes", "replaces", "unknown_ref", "duplicate_ref",
             "overfill", "zero_add")


class RefBooks:
    def __init__(self) -> None:
        self.orders: dict[int, list] = {}  # ref -> [locate, side, price, shares, seq]
        # levels[locate][side][price] -> {ref: None} (insertion-ordered = FIFO)
        self.levels = defaultdict(lambda: {"B": defaultdict(dict), "S": defaultdict(dict)})
        self.stats = dict.fromkeys(STAT_KEYS, 0)

    def add(self, ref, locate, side, shares, price, count=True):
        if shares == 0:
            self.stats["zero_add"] += 1
            return None
        if ref in self.orders:
            self.stats["duplicate_ref"] += 1
            return None
        self.orders[ref] = [locate, side, price, shares]
        self.levels[locate][side][price][ref] = None
        if count:
            self.stats["adds"] += 1
        return locate

    def _unlink(self, ref):
        locate, side, price, _ = self.orders.pop(ref)
        lv = self.levels[locate][side]
        del lv[price][ref]
        if not lv[price]:
            del lv[price]

    def reduce(self, ref, shares, is_exec):
        o = self.orders.get(ref)
        if o is None:
            self.stats["unknown_ref"] += 1
            return None
        if shares > o[3]:
            self.stats["overfill"] += 1
            shares = o[3]
        self.stats["executions" if is_exec else "cancels"] += 1
        locate = o[0]
        if shares == o[3]:
            self._unlink(ref)
        else:
            o[3] -= shares
        return locate

    def remove(self, ref):
        o = self.orders.get(ref)
        if o is None:
            self.stats["unknown_ref"] += 1
            return None
        self._unlink(ref)
        self.stats["deletes"] += 1
        return o[0]

    def replace(self, old, new, shares, price):
        o = self.orders.get(old)
        if o is None:
            self.stats["unknown_ref"] += 1
            return None
        locate, side = o[0], o[1]
        self._unlink(old)
        self.stats["replaces"] += 1
        self.add(new, locate, side, shares, price, count=False)
        return locate

    # --- views ---------------------------------------------------------------------------
    def depth(self, locate, side, n):
        """Top-n levels as (price, qty, count), best first."""
        lv = self.levels[locate][side] if locate in self.levels else {}
        prices = sorted(lv, reverse=(side == "B"))[:n]
        out = []
        for p in prices:
            refs = lv[p]
            out.append((p, sum(self.orders[r][3] for r in refs), len(refs)))
        return out

    def queue(self, locate, side, price):
        """Order refs at a level, front of queue first."""
        return list(self.levels[locate][side].get(price, {}))


class RefReplayer:
    def __init__(self) -> None:
        self.books = RefBooks()
        self.digest = DIGEST_INIT
        self.messages = 0
        self.trades = 0
        self.bad = 0
        self.symbols: dict[int, str] = {}

    def _word(self, w):
        self.digest = ((self.digest ^ (w & MASK)) * DIGEST_PRIME) & MASK

    def _fold(self, locate):
        bid = self.books.depth(locate, "B", 1)
        ask = self.books.depth(locate, "S", 1)
        for w in (self.messages, locate,
                  bid[0][0] if bid else 0, bid[0][1] if bid else 0,
                  ask[0][0] if ask else 0, ask[0][1] if ask else 0):
            self._word(w)

    def feed(self, buf: bytes) -> None:
        b = self.books
        for m in decode(buf):
            t = m.type
            touched = None
            if t == "A":
                ref, side, shares, price = m.fields
                touched = b.add(ref, m.locate, "B" if side == "B" else "S", shares, price)
            elif t == "E":
                touched = b.reduce(m.fields[0], m.fields[1], True)
            elif t == "X":
                touched = b.reduce(m.fields[0], m.fields[1], False)
            elif t == "D":
                touched = b.remove(m.fields[0])
            elif t == "U":
                touched = b.replace(*m.fields)
            elif t == "P":
                self.trades += 1
            elif t == "R":
                self.symbols[m.locate] = m.fields[0]
            elif t in ("?", "\0"):
                self.bad += 1
            if touched is not None:
                self._fold(touched)
            self.messages += 1
