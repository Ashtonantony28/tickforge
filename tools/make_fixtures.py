#!/usr/bin/env python3
"""Regenerate the committed synthetic ITCH fixtures and their expected results (ADR 0004).

Expected values come from the Python reference book, never from the C++ engine, so the fixture
check in ctest is a genuine cross-implementation comparison. Regenerating is a reviewed change.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from tickforge import refbook, synth  # noqa: E402

FIXTURES = {
    "synth_small": dict(n_messages=20_000, seed=7, n_stocks=6, fault_rate=0.0),
    "synth_faults": dict(n_messages=8_000, seed=11, n_stocks=3, fault_rate=0.04),
}
DEPTH = 5


def expected(buf: bytes) -> dict:
    r = refbook.RefReplayer()
    r.feed(buf)
    books = {}
    for loc in sorted(r.books.levels):
        b, s = r.books.depth(loc, "B", DEPTH), r.books.depth(loc, "S", DEPTH)
        if b or s:
            books[str(loc)] = {"B": [list(x) for x in b], "S": [list(x) for x in s]}
    return {
        "messages": r.messages,
        "digest": f"{r.digest:016x}",
        "live_orders": len(r.books.orders),
        "bad_messages": r.bad,
        "trades": r.trades,
        **r.books.stats,
        "depth": DEPTH,
        "books": books,
    }


def main() -> int:
    out = ROOT / "fixtures"
    out.mkdir(exist_ok=True)
    for stem, kw in FIXTURES.items():
        buf = synth.generate(**kw)
        (out / f"{stem}.itch").write_bytes(buf)
        exp = {"generator": kw, **expected(buf)}
        (out / f"{stem}.expected.json").write_text(json.dumps(exp, indent=1) + "\n")
        print(stem, len(buf), "bytes", exp["digest"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
