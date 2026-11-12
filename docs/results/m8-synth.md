# M8 results: open-source FPGA synthesis of the ITCH feed handler

Flow: `synth/synth_ecp5.sh` = Yosys 0.70 with its built-in slang SystemVerilog frontend, then
nextpnr-ecp5, both the YoWASP pip builds (`pip install yowasp-yosys yowasp-nextpnr-ecp5`), target
Lattice ECP5-85F (LFE5U-85F, CABGA381). The core is placed and routed out of context (no IO
buffers): it has ~460 debug/record ports and would sit behind a MAC/DMA wrapper on a board. The RTL is
exactly the M7 RTL that is bit-exact with the C++ engine; nothing was changed for synthesis.

ADR 0003 made the feed handler simulation-first, and ADR 0007 already listed what must change for
an FPGA. These runs measure how far the simulation design is from that.

## Results

| Config | LUT4 | FFs | Distributed RAM (DPR16X4) | Block RAM | Fits ECP5-85F? | Fmax (seed 1) |
|---|---|---|---|---|---|---|
| AW=8 (256 orders), LEVELS=16 | 16,186 (21,622 slices' COMB, 25%) | 4,396 (5%) | 528 | 0 | yes | **22.0 MHz** |
| AW=12 (4096 orders), LEVELS=64 (M7 default) | 85,012 (COMB 144,002, **172%**) | 19,096 | 8,448 (80%) | 0 | **no** | not routed |

**The M7 design is a correct reference, not an FPGA design yet.** At 22 MHz and 1 byte/cycle it
would handle about 0.7M ITCH messages/s and ~180 ns median tick-to-book (4 cycles), far from the
250 MHz assumed in the M7 write-up, which is now corrected.

## Why (from the timing and utilisation reports)

1. **Critical path (45 ns): the price-level insert position.** `tf_price_levels` computes `ins_pos`
   as a count over all held levels (a chain of comparators feeding an adder chain: 135 logic and
   routing stages), and that count drives the shift enables of every level register. Fix: compare
   every level in parallel, derive a one-hot shift mask from the sorted prefix (no count), and
   register the match before the update (one extra cycle of latency).
2. **No block RAM at all.** The order table has an asynchronous read port and a reset loop over every
   slot, so Yosys cannot map it to DP16KD block RAM and builds it from LUT RAM plus muxes; at 4096
   slots that alone overflows the device. Fix: synchronous read (one more probe cycle) and a
   generation counter instead of resetting every slot.
3. **Register-array levels.** 2 x 64 levels of 32-bit price and 40-bit quantity in flops with
   comparators on each is large; keeping only the top N (for example 16) in registers and the rest
   in block RAM is the usual split.

These are the next RTL steps; each must keep the cocotb bench bit-exact against the C++ engine.
Board bring-up (on-board replay and measured latency) still needs hardware.

## Reproduce
```bash
pip install yowasp-yosys yowasp-nextpnr-ecp5
synth/synth_ecp5.sh 8 16 200     # ~25 min on one core; reports in build-synth/
synth/synth_ecp5.sh 12 64 50     # M7 default: synthesis ~11 min, placement fails (does not fit)
```
