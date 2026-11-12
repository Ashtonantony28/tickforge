# M7 results: SystemVerilog ITCH feed handler (simulation)

RTL: `rtl/tf_itch_book.sv` and submodules (ADR 0007). Bench: `verif/test_itch_book.py` (cocotb 2.1,
Verilator 5.049). Defaults: AW=12 (4096-slot order table), PROBES=16, LEVELS=64.

## Bit-exact vs the C++ engine

Every top-of-book record the RTL emits (message index, best bid price/qty, best ask price/qty) is
compared with the engine's trace for the same instrument, record by record, and folded into the
shared digest.

| Stream | Locate | Input gaps | Updates | RTL digest = engine | Capacity faults |
|---|---|---|---|---|---|
| synth_small (20k msgs) | 1 | 0% | 3,302 | `1b7e10833777a05d` yes | 0 |
| synth_small | 2 | 0% | all | yes | 0 |
| synth_small | 3 | 25% | all | yes | 0 |
| synth_faults (8k msgs, 4% malformed) | 1 | 0% | all | yes | 0 |
| synth_faults | 2 | 10% | 2,456 | `78508e9f41542a3f` yes | 0 |
| fresh random 40k msgs, 1% malformed | 1 | 5% | all | yes | 0 |

On synth_faults locate 2 the RTL saw and handled the same way as the engine: 20 unknown refs, 20
duplicate refs, 22 zero-share adds and 11 over-fills.

**The bench catches injected bugs.** Two hand-injected bugs were caught on the first mismatching
record: removing the over-fill clamp failed on synth_faults, and shifting the add-order price field
by one byte failed on record 0.

## Performance (cycle-accurate)

| Metric | Value |
|---|---|
| Sustained input | 1.0 byte/cycle (no back-pressure with gap-free input) |
| Latency, last byte of message → top-of-book out | min 4, median 4, max 10 cycles |

At an assumed 250 MHz this is about 7M ITCH messages/s per instance and 16 ns median
tick-to-book. **Fmax has not been measured:** there is no synthesis run yet, and ADR 0007 lists
what must change for an FPGA.

## Reproduce
```bash
pip install verilator cocotb
cmake --build build              # the bench uses the C++ engine via pybind11
python -m pytest -q tests/test_rtl.py -s
```
