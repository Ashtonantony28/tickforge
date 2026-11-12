# 0011: Paper trading daemon: record-driven engine, replay equality, hash-chained log

- **Status:** Accepted
- **Date:** 2026-11-12

## Context
M6 runs a market maker against the live public Binance feed with simulated fills only
(CLAUDE.md rule 4). The plan asks for daemon uptime, log integrity and a live-vs-backtest
comparison.

## Decision
- **Record-driven.** Every feed payload is written to the raw recording (tickforge.recorder format)
  and then passed to `PaperEngine.process`, which uses only the record's timestamp. No wall-clock
  reads in the engine, so replaying the recording through the same code must reproduce the live
  decision log line for line. That equality *is* the live-vs-backtest comparison: any difference
  means the strategy depended on something the backtest cannot see.
- **Hash-chained log.** Each log line stores SHA-256(previous hash + body); `verify` recomputes the
  chain and replays the recording.
- **Book:** the M3 `BinanceSync` (same sync, gap and snapshot checks as offline). While unsynced the
  engine pulls all quotes.
- **Fill model (L2, conservative):** queue ahead = displayed size at our price when the order goes
  live (after 50 ms simulated latency); trades at our price consume it before filling us; a trade
  through our price or the opposite touch reaching it fills us; size decreases without trades only
  shrink "ahead" down to the new level size (cancels assumed to come from behind us).
- **Redundant feed:** the recorder holds two websocket connections and keeps the first copy of each
  update id / trade id, so one dropped connection is not a gap. A reconnect is no longer treated as
  a gap by itself; the `U == u + 1` check decides.
- **Endpoints:** keyless `*.binance.vision` market-data hosts only; there is no order-entry code.

## Consequences
- Replay equality holds only for the code version that ran live; changing the engine changes the
  log (seen in the first run, see docs/results/m6-paper.md).
- A run killed mid-write can lose the unflushed tail of the recording (gzip flush every 5 s), so
  replay may end a few lines early; `verify` reports that separately.
- The cloud container restarts every 1 to 2 hours, so multi-day runs need a host that stays up.

## Alternatives considered
- **Wall-clock timers for requotes:** simpler, but breaks replay equality.
- **Recording only the engine's view:** smaller, but loses the ability to re-run a changed strategy.
