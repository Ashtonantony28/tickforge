# 0003: FPGA feed handler is simulation-first

- **Status:** Accepted
- **Date:** 2026-11-12

## Context
Real latency numbers need a board; correctness does not.

## Decision
M7 targets Verilator + cocotb simulation, bit-exact against the C++ engine, with cycle-level latency.
Board bring-up (M8) is optional and decided later; RTL stays vendor-neutral.

## Consequences
Reported latency is in cycles at a synthesis-estimated Fmax until a board exists.
