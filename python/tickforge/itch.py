"""ITCH 5.0 binary encoding/decoding for the message types Tickforge uses.

Framing matches Nasdaq sample files: 2-byte big-endian length, then the payload.
Field layouts follow the TotalView-ITCH 5.0 specification.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterator

LENGTHS = {
    "S": 12, "R": 39, "H": 25, "Y": 20, "L": 26, "V": 35, "W": 12, "K": 28, "J": 35, "h": 21,
    "A": 36, "F": 40, "E": 31, "C": 36, "X": 23, "D": 19, "U": 35, "P": 44, "Q": 40, "B": 19,
    "I": 50, "N": 20, "O": 48,
}


def _hdr(t: str, locate: int, tracking: int, ts: int) -> bytes:
    return t.encode() + struct.pack(">HH", locate, tracking) + ts.to_bytes(6, "big")


def _stock(sym: str) -> bytes:
    return sym.encode().ljust(8, b" ")[:8]


def frame(payload: bytes) -> bytes:
    assert LENGTHS[chr(payload[0])] == len(payload), (chr(payload[0]), len(payload))
    return struct.pack(">H", len(payload)) + payload


def system_event(ts: int, event: str) -> bytes:
    return frame(_hdr("S", 0, 0, ts) + event.encode())


def stock_directory(ts: int, locate: int, sym: str, round_lot: int = 100) -> bytes:
    body = (_stock(sym) + b"Q" + b"N" + struct.pack(">I", round_lot) + b"N" + b"C" + b"Z " + b"P"
            + b"N" + b"N" + b"1" + b"N" + struct.pack(">I", 0) + b"N")
    return frame(_hdr("R", locate, 0, ts) + body)


def add(ts: int, locate: int, ref: int, side: str, shares: int, sym: str, price: int, mpid: str | None = None) -> bytes:
    body = struct.pack(">Q", ref) + side.encode() + struct.pack(">I", shares) + _stock(sym) + struct.pack(">I", price)
    if mpid is None:
        return frame(_hdr("A", locate, 0, ts) + body)
    return frame(_hdr("F", locate, 0, ts) + body + mpid.encode().ljust(4, b" ")[:4])


def executed(ts: int, locate: int, ref: int, shares: int, match: int) -> bytes:
    return frame(_hdr("E", locate, 0, ts) + struct.pack(">QIQ", ref, shares, match))


def executed_price(ts: int, locate: int, ref: int, shares: int, match: int, price: int, printable: str = "Y") -> bytes:
    return frame(_hdr("C", locate, 0, ts) + struct.pack(">QIQ", ref, shares, match) + printable.encode() + struct.pack(">I", price))


def cancel(ts: int, locate: int, ref: int, shares: int) -> bytes:
    return frame(_hdr("X", locate, 0, ts) + struct.pack(">QI", ref, shares))


def delete(ts: int, locate: int, ref: int) -> bytes:
    return frame(_hdr("D", locate, 0, ts) + struct.pack(">Q", ref))


def replace(ts: int, locate: int, old_ref: int, new_ref: int, shares: int, price: int) -> bytes:
    return frame(_hdr("U", locate, 0, ts) + struct.pack(">QQII", old_ref, new_ref, shares, price))


def trade(ts: int, locate: int, ref: int, side: str, shares: int, sym: str, price: int, match: int) -> bytes:
    body = struct.pack(">Q", ref) + side.encode() + struct.pack(">I", shares) + _stock(sym) + struct.pack(">IQ", price, match)
    return frame(_hdr("P", locate, 0, ts) + body)


@dataclass(frozen=True)
class Msg:
    type: str
    locate: int
    ts: int
    fields: tuple


def decode(buf: bytes) -> Iterator[Msg]:
    """Yield decoded messages. Unknown/other types come back with empty fields."""
    off, n = 0, len(buf)
    while off + 2 <= n:
        (ln,) = struct.unpack_from(">H", buf, off)
        if off + 2 + ln > n:
            break
        m = buf[off + 2: off + 2 + ln]
        off += 2 + ln
        if ln == 0:
            yield Msg("\0", 0, 0, ())
            continue
        t = chr(m[0])
        if LENGTHS.get(t) != ln:
            yield Msg("?", 0, 0, (t, ln))
            continue
        locate, _tracking = struct.unpack_from(">HH", m, 1)
        ts = int.from_bytes(m[5:11], "big")
        if t in ("A", "F"):
            ref, = struct.unpack_from(">Q", m, 11)
            side = chr(m[19])
            shares, = struct.unpack_from(">I", m, 20)
            price, = struct.unpack_from(">I", m, 32)
            yield Msg("A", locate, ts, (ref, side, shares, price))
        elif t in ("E", "C"):
            ref, shares = struct.unpack_from(">QI", m, 11)
            yield Msg("E", locate, ts, (ref, shares))
        elif t == "X":
            yield Msg("X", locate, ts, struct.unpack_from(">QI", m, 11))
        elif t == "D":
            yield Msg("D", locate, ts, struct.unpack_from(">Q", m, 11))
        elif t == "U":
            yield Msg("U", locate, ts, struct.unpack_from(">QQII", m, 11))
        elif t == "R":
            yield Msg("R", locate, ts, (m[11:19].decode().rstrip(),))
        else:
            yield Msg(t, locate, ts, ())
