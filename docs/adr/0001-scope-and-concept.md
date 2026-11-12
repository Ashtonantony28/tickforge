# 0001: Scope: order-book research stack, paper trading only

- **Status:** Accepted
- **Date:** 2026-11-12

## Context
Three concepts were offered in the project plan: (A) an order-book stack spanning C++ engine, ML alpha,
market making and an FPGA feed handler; (B) a pure ML alpha platform on bars; (C) an RL market-making
simulator. The owner chose the recommended option A.

## Decision
Build concept A. Markets: Nasdaq ITCH 5.0 equities (L3) and crypto L2. Real money and order submission
to real venues are permanently out of scope.

## Consequences
The C++ engine is the system's golden model; Python research and RTL are checked against it. Milestones
M4/M5 (research, strategy) and M7 (RTL) both depend only on M1-M2 and can proceed in parallel.

## Alternatives considered
B and C are absorbed as milestones (M4 alpha research, M5 learned market maker).
