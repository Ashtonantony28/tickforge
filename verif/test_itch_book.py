"""cocotb testbench for rtl/tf_itch_book.sv (M7, ADR 0007).

Streams an ITCH file through the RTL one byte per cycle (optionally with random idle gaps) and
compares every top-of-book record the RTL emits against the C++ engine's trace for the same
instrument: message index, best bid/ask price and quantity, after every single update. Also checks
that no capacity fault occurred and reports per-message latency in cycles.

Configured by environment variables (set by tests/test_rtl.py):
  TF_ITCH_FILE  path to the ITCH file
  TF_LOCATE     instrument (stock locate) the RTL tracks
  TF_GAP_PCT    percent of cycles with s_valid low (default 0)
  TF_SEED       RNG seed for gaps
  TF_RESULT     optional path to write a JSON summary
"""
from __future__ import annotations

import json
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from tickforge import load_engine
from tickforge.refbook import DIGEST_INIT, DIGEST_PRIME, MASK


def _digest(events, locate):
    h = DIGEST_INIT
    for idx, bp, bq, ap, aq in events:
        for w in (idx, locate, bp, bq, ap, aq):
            h = ((h ^ w) * DIGEST_PRIME) & MASK
    return h


def _message_ends(buf):
    """Byte offset of the last byte of each message, in order."""
    ends, off = [], 0
    while off + 2 <= len(buf):
        n = int.from_bytes(buf[off:off + 2], "big")
        off += 2 + n
        ends.append(off - 1)
    return ends


@cocotb.test()
async def itch_book_matches_engine(dut):
    path = os.environ["TF_ITCH_FILE"]
    locate = int(os.environ.get("TF_LOCATE", "1"))
    gap = int(os.environ.get("TF_GAP_PCT", "0"))
    rng = random.Random(int(os.environ.get("TF_SEED", "1")))
    buf = open(path, "rb").read()

    eng = load_engine().Replayer()
    eng.set_trace_locate(locate)
    eng.feed(buf)
    expected = eng.trace

    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    dut.rst.value = 1
    dut.s_valid.value = 0
    dut.s_data.value = 0
    dut.cfg_locate.value = locate
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst.value = 0

    cycle = 0
    got = []
    emit_cycle = {}
    accept_cycle = {}
    ends = _message_ends(buf)
    end_to_index = {e: i for i, e in enumerate(ends)}

    async def monitor():
        nonlocal cycle
        while True:
            await RisingEdge(dut.clk)
            cycle += 1
            await ReadOnly()
            if dut.tob_valid.value:
                rec = (int(dut.tob_index.value), int(dut.tob_bid_px.value), int(dut.tob_bid_qty.value),
                       int(dut.tob_ask_px.value), int(dut.tob_ask_qty.value))
                got.append(rec)
                emit_cycle[rec[0]] = cycle

    cocotb.start_soon(monitor())

    pos = 0
    start = cycle
    while pos < len(buf):
        await FallingEdge(dut.clk)
        if gap and rng.randrange(100) < gap:
            dut.s_valid.value = 0
            continue
        dut.s_valid.value = 1
        dut.s_data.value = buf[pos]
        await ReadOnly()
        ready = bool(dut.s_ready.value)
        await RisingEdge(dut.clk)
        if ready:
            if pos in end_to_index:
                accept_cycle[end_to_index[pos]] = cycle + 1  # monitor increments on this edge
            pos += 1
    await FallingEdge(dut.clk)
    dut.s_valid.value = 0
    stream_cycles = cycle - start
    for _ in range(64):
        await RisingEdge(dut.clk)

    faults = {k: int(getattr(dut, k).value) for k in
              ("cnt_table_full", "cnt_level_overflow", "cnt_level_miss")}
    counters = {k: int(getattr(dut, k).value) for k in
                ("cnt_unknown_ref", "cnt_duplicate_ref", "cnt_zero_add", "cnt_overfill", "cnt_bad_len")}
    lat = sorted(emit_cycle[i] - accept_cycle[i] for i in emit_cycle if i in accept_cycle)
    summary = {
        "file": os.path.basename(path), "locate": locate, "gap_pct": gap, "bytes": len(buf),
        "messages": len(ends), "updates": len(got), "stream_cycles": stream_cycles,
        "bytes_per_cycle": round(len(buf) / max(stream_cycles, 1), 4),
        "latency_cycles": {"min": lat[0], "median": lat[len(lat) // 2], "max": lat[-1]} if lat else None,
        "digest": f"{_digest(got, locate):016x}", "expected_digest": f"{_digest(expected, locate):016x}",
        "faults": faults, "counters": counters,
    }
    dut._log.info(json.dumps(summary))
    if os.environ.get("TF_RESULT"):
        with open(os.environ["TF_RESULT"], "w") as f:
            json.dump(summary, f, indent=1)

    assert faults == {"cnt_table_full": 0, "cnt_level_overflow": 0, "cnt_level_miss": 0}, faults
    for i, (g, e) in enumerate(zip(got, expected)):
        assert g == tuple(e), f"record {i}: rtl {g} != engine {tuple(e)}"
    assert len(got) == len(expected), f"rtl emitted {len(got)} updates, engine {len(expected)}"
