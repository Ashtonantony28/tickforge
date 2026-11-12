#!/usr/bin/env python3
"""Download market-data sources into data/raw/ and verify SHA-256 (ADR 0004).

Usage:
  python data/fetch.py itch-sample [--date 01302019] [--pin]
  python data/fetch.py binance-trades --symbol BTCUSDT --date 2024-01-02 [--pin]

Nothing downloaded here is ever committed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
CHECKSUMS = ROOT / "checksums.json"

SOURCES = {
    "itch-sample": lambda a: (
        f"https://emi.nasdaq.com/ITCH/Nasdaq%20ITCH/{a.date}.NASDAQ_ITCH50.gz",
        f"itch/{a.date}.NASDAQ_ITCH50.gz",
    ),
    "binance-trades": lambda a: (
        f"https://data.binance.vision/data/spot/daily/trades/{a.symbol}/{a.symbol}-trades-{a.date}.zip",
        f"binance/{a.symbol}-trades-{a.date}.zip",
    ),
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", choices=sorted(SOURCES))
    p.add_argument("--date", default="01302019")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--pin", action="store_true", help="record the checksum if none is pinned yet")
    a = p.parse_args(argv)

    url, rel = SOURCES[a.source](a)
    dest = RAW / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        print(f"downloading {url}", file=sys.stderr)
        tmp = dest.with_suffix(dest.suffix + ".part")
        urllib.request.urlretrieve(url, tmp)
        tmp.rename(dest)
    digest = sha256(dest)
    if a.source == "binance-trades":
        # Binance publishes a SHA-256 next to every dump file; check it before pinning anything.
        with urllib.request.urlopen(url + ".CHECKSUM", timeout=30) as r:
            published = r.read().decode().split()[0]
        if published != digest:
            print(f"CHECKSUM MISMATCH vs Binance's published value for {rel}", file=sys.stderr)
            return 1
    sums = json.loads(CHECKSUMS.read_text())
    pinned = sums.get(rel)
    if pinned is None:
        if a.pin:
            sums[rel] = digest
            CHECKSUMS.write_text(json.dumps(sums, indent=2, sort_keys=True) + "\n")
            print(f"pinned {rel} {digest}")
            return 0
        print(f"WARNING: no pinned checksum for {rel} (sha256 {digest}); rerun with --pin", file=sys.stderr)
        return 0
    if pinned != digest:
        print(f"CHECKSUM MISMATCH for {rel}: expected {pinned}, got {digest}", file=sys.stderr)
        return 1
    print(f"ok {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
