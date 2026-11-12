# 0007: RTL feed handler: byte-serial parser, hashed order table, register level arrays

- **Status:** Accepted
- **Date:** 2026-11-12

## Context
M7 puts the ITCH-to-book hot path in SystemVerilog, verified bit-exact against the C++ engine
(simulation first, ADR 0003). It must be simple enough to verify thoroughly and be a credible
starting point for an FPGA.

## Decision
- **One instrument per instance** (`cfg_locate`); multiple symbols = multiple instances behind a
  locate demux. Messages are filtered on the header's stock locate.
- **Parser** accepts one byte per cycle, extracts fields by byte offset, validates spec lengths, and
  counts every message so its index matches the engine's.
- **Order table**: 2^AW-slot open-addressing memory keyed by order ref (xor-fold hash), linear
  probing with tombstones, bounded at PROBES. Exceeding it raises `cnt_table_full` (must be 0).
- **Price levels**: per side, LEVELS entries in registers, sorted best-first; insert/remove with a
  one-cycle parallel shift. Overflow raises `cnt_level_overflow` (must be 0).
- **Output**: one top-of-book record per book-changing message, which the testbench folds into the
  engine's digest. Error semantics (zero-share adds, duplicate refs, unknown refs, over-fill clamp)
  mirror `MarketBooks` exactly.
- The order table uses a combinational read for one probe per cycle in simulation.

## Consequences
- 1 byte/cycle sustained and 4 cycles median from a message's last byte to its top-of-book update
  (see docs/results/m7-rtl.md).
- Not yet FPGA-ready: the table's combinational read must become a registered BRAM read (adds a
  pipeline stage per probe), and the 64-entry parallel shift will limit Fmax. Both are M8 work,
  measured against a synthesis report once a board/toolchain is chosen.
- Tombstones are never reclaimed except by insert reuse; a full trading day needs periodic rebuild
  or backward-shift deletion in hardware (M8).

## Alternatives considered
- 8-byte-wide datapath (higher throughput, more complex field alignment): deferred to M8.
- CAM for the order table: fastest, far too large for realistic order counts.
