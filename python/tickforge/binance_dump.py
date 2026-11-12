"""Binance public data dump ingestion (M3): daily spot trades zip -> exact integer numpy arrays.

CSV columns (no header): trade_id, price, qty, quote_qty, time, is_buyer_maker, is_best_match.
`time` is microseconds since 2025-01-01 for spot dumps and milliseconds before; both are
normalised to integer nanoseconds. Prices/quantities use the same 1e-8 fixed point as the L2
book (tickforge.l2.parse_dec), so trades and book states compare exactly.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np

from .l2 import parse_dec

TRADE_DTYPE = np.dtype([("id", "<i8"), ("t_ns", "<i8"), ("price", "<i8"), ("qty", "<i8"),
                        ("buyer_maker", "?")])
_US_CUTOFF = 10**15  # >= 1e15 means microseconds (ms epoch stays below until year 33658)


def parse_trades_csv(text: io.TextIOBase) -> np.ndarray:
    rows = []
    for line in text:
        f = line.rstrip("\n").split(",")
        if len(f) < 6 or not f[0].isdigit():
            continue  # header or blank
        t = int(f[4])
        t_ns = t * 1000 if t >= _US_CUTOFF else t * 1_000_000
        rows.append((int(f[0]), t_ns, parse_dec(f[1]), parse_dec(f[2]), f[5].strip().lower() == "true"))
    a = np.array(rows, dtype=TRADE_DTYPE)
    if len(a) and (np.diff(a["id"]) <= 0).any():
        raise ValueError("trade ids not strictly increasing")
    return a


def load_trades(path: Path) -> np.ndarray:
    with zipfile.ZipFile(path) as z:
        (name,) = [n for n in z.namelist() if n.endswith(".csv")]
        with z.open(name) as f:
            return parse_trades_csv(io.TextIOWrapper(f, encoding="ascii"))


def summary(a: np.ndarray) -> dict:
    ids = a["id"]
    return {"trades": int(len(a)), "first_id": int(ids[0]), "last_id": int(ids[-1]),
            "missing_ids": int(ids[-1] - ids[0] + 1 - len(a)),
            "buyer_maker_frac": round(float(a["buyer_maker"].mean()), 4),
            "volume": float(a["qty"].sum() / 1e8),
            "t0_ns": int(a["t_ns"][0]), "t1_ns": int(a["t_ns"][-1])}


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(summary(load_trades(Path(sys.argv[1]))), indent=2))
