# 0006: Backtester fill model: exact L3 queue position, no market impact, latency on every action

- **Status:** Accepted
- **Date:** 2026-11-12

## Context
Queue-based strategies (market making) live or die on fill realism. Bar- or L2-based backtesters
must guess queue position; with L3 ITCH data we know it.

## Decision
- Each exchange order carries an arrival sequence number (`Order::seq`). A simulated passive order
  records the book's next sequence number when it joins; exchange orders with a smaller sequence at
  the same price/side are ahead of it. `ahead` starts at the level's displayed size and is reduced by
  executions, cancels, deletes and replaces of those orders.
- Passive fills happen when (a) an order that arrived after ours executes at our price (the queue
  reached us; we fill up to the executed size), (b) an execution happens at a worse price on our side
  (trade-through: full fill), or (c) the opposite touch reaches our price (full fill).
- Aggressive orders walk visible opposite levels up to their limit. Post-only orders that would
  cross on arrival are rejected; IOC remainders are dropped.
- **No market impact:** simulated orders never enter or remove liquidity from the exchange book.
  Repeated aggression against the same displayed size is therefore optimistic; strategies that take
  liquidity must be sized well below displayed depth, and M5 reports sensitivity.
- Every submit and cancel reaches the exchange after `latency_ns` (+ seeded jitter); a cancel that
  arrives after a fill is rejected, as on a real venue.
- Money is integer (1e-4 $); maker/taker fees per share; the fill log is digested for reproducibility.
- Risk checks (max order size, worst-case position per side including in-flight orders) run in the
  simulator, not the strategy.

## Consequences
Exact for the displayed book. Hidden/reserve liquidity and our own impact are not modelled, which
biases passive fill rates slightly up for small orders; M5's calibration plot measures this on
held-out data.

## Alternatives considered
Probabilistic queue models (needed only for L2 data; used for crypto in M3); full impact model
(inserting simulated orders into the book) deferred until a strategy needs it.
