"""Market-data-only L2 recorder for Binance spot (M3, ADR 0008).

Records the public diff-depth and trade streams plus periodic REST depth snapshots to hourly
gzip JSONL files. Each line is {"t": local_receive_ns, "k": kind, "d": payload} with kind in
{depth, trade, snap, meta}. The raw venue payload is stored untouched; all parsing and
sequence-number sync happens offline in tickforge.l2.BinanceSync, so a recording can always be
re-interpreted.

Endpoints are Binance's market-data-only hosts (data-stream / data-api .binance.vision): they
serve public data and accept no orders or keys. This module never authenticates (CLAUDE.md rule 4).

    python -m tickforge.recorder --symbol BTCUSDT --out data/raw/rec --hours 24
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import signal
import sys
import time
import urllib.request
from pathlib import Path

WS_URL = "wss://data-stream.binance.vision/stream?streams={s}@depth@100ms/{s}@trade"
REST_URL = "https://data-api.binance.vision/api/v3/depth?symbol={S}&limit={n}"


class RotatingWriter:
    def __init__(self, out: Path, symbol: str) -> None:
        self.out, self.symbol = out, symbol
        self.f = None
        self.hour = None
        self.lines = 0
        out.mkdir(parents=True, exist_ok=True)

    def write(self, t_ns: int, kind: str, payload) -> None:
        hour = time.strftime("%Y%m%dT%H", time.gmtime(t_ns / 1e9))
        if hour != self.hour:
            self.close()
            self.hour = hour
            self.f = gzip.open(self.out / f"{self.symbol}-{hour}.jsonl.gz", "at", compresslevel=6)
        self.f.write(json.dumps({"t": t_ns, "k": kind, "d": payload}, separators=(",", ":")) + "\n")
        self.lines += 1

    def flush(self) -> None:
        if self.f:
            self.f.flush()  # gzip sync flush: a crash loses at most the unflushed tail

    def close(self) -> None:
        if self.f:
            self.f.close()
            self.f = None


def fetch_snapshot(symbol: str, limit: int) -> dict:
    with urllib.request.urlopen(REST_URL.format(S=symbol.upper(), n=limit), timeout=20) as r:
        return json.loads(r.read())


class Recorder:
    def __init__(self, symbol: str, out: Path, hours: float, snapshot_every: float, limit: int) -> None:
        self.symbol = symbol.lower()
        self.w = RotatingWriter(out, symbol.upper())
        self.deadline = time.monotonic() + hours * 3600
        self.snapshot_every = snapshot_every
        self.limit = limit
        self.stop = asyncio.Event()
        self.last_u: int | None = None
        self.need_snapshot = True
        self.stats = {"depth": 0, "trade": 0, "snap": 0, "live_gaps": 0, "connects": 0, "errors": 0}

    def meta(self, **kw) -> None:
        self.w.write(time.time_ns(), "meta", kw)

    async def snapshot(self) -> None:
        try:
            snap = await asyncio.to_thread(fetch_snapshot, self.symbol, self.limit)
        except Exception as e:  # network hiccup: log and retry on the next tick
            self.stats["errors"] += 1
            self.meta(event="snapshot_error", error=repr(e))
            return
        self.w.write(time.time_ns(), "snap", snap)
        self.stats["snap"] += 1
        self.need_snapshot = False

    async def snapshot_loop(self) -> None:
        next_t = 0.0
        while not self.stop.is_set():
            if self.need_snapshot or time.monotonic() >= next_t:
                await asyncio.sleep(1.0 if self.need_snapshot else 0)  # let a few diffs buffer first
                await self.snapshot()
                next_t = time.monotonic() + self.snapshot_every
            await asyncio.sleep(0.5)

    async def stream_loop(self) -> None:
        import websockets  # optional dependency, only needed for live recording

        backoff = 1.0
        while not self.stop.is_set():
            try:
                async with websockets.connect(WS_URL.format(s=self.symbol), ping_interval=20,
                                              max_size=2**24) as ws:
                    self.stats["connects"] += 1
                    self.meta(event="connect", url=WS_URL.format(s=self.symbol))
                    self.last_u = None
                    self.need_snapshot = True
                    backoff = 1.0
                    async for raw in ws:
                        t = time.time_ns()
                        msg = json.loads(raw)
                        d = msg.get("data", msg)
                        kind = "depth" if d.get("e") == "depthUpdate" else "trade" if d.get("e") == "trade" else "other"
                        self.w.write(t, kind, d)
                        self.stats[kind] = self.stats.get(kind, 0) + 1
                        if kind == "depth":
                            if self.last_u is not None and d["U"] != self.last_u + 1:
                                self.stats["live_gaps"] += 1
                                self.meta(event="live_gap", U=d["U"], last_u=self.last_u)
                                self.need_snapshot = True
                            self.last_u = d["u"]
                        if self.stop.is_set():
                            break
            except Exception as e:
                self.stats["errors"] += 1
                self.meta(event="disconnect", error=repr(e))
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def housekeeping(self) -> None:
        while not self.stop.is_set():
            await asyncio.sleep(5)
            self.w.flush()
            if time.monotonic() >= self.deadline:
                self.stop.set()

    async def run(self) -> dict:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.stop.set)
        self.meta(event="start", symbol=self.symbol)
        tasks = [asyncio.create_task(c) for c in (self.stream_loop(), self.snapshot_loop(), self.housekeeping())]
        await self.stop.wait()
        await self.snapshot()  # final snapshot closes the last check window
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.meta(event="stop", stats=self.stats)
        self.w.close()
        return self.stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--out", type=Path, default=Path("data/raw/rec"))
    p.add_argument("--hours", type=float, default=24.0)
    p.add_argument("--snapshot-every", type=float, default=300.0, help="seconds between REST checks")
    p.add_argument("--limit", type=int, default=1000, help="REST snapshot depth")
    a = p.parse_args(argv)
    stats = asyncio.run(Recorder(a.symbol, a.out, a.hours, a.snapshot_every, a.limit).run())
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
