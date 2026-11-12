# 0010: Market maker: Avellaneda-Stoikov with an imbalance skew, on the L3 backtester

- **Status:** Accepted
- **Date:** 2026-11-12

## Context
M5 needs a baseline market maker, an alpha-aware variant, a risk layer and an honest P&L
decomposition, all evaluated on held-out real data with the ADR 0006 fill model.

## Decision
- **Quote rule:** Avellaneda-Stoikov (2008) with a fixed horizon tau (infinite-horizon variant),
  EWMA volatility of 1 s mid changes, reservation price leaning against inventory, quotes rounded
  outward to the tick and never crossing. Post-only orders, requotes rate-limited to 1 ms per
  side, cancel acknowledged before the replacement goes out (no double exposure).
- **Alpha skew:** both quotes shift by `alpha_ticks * imb1` ticks, where imb1 is L1 queue
  imbalance, the strongest single feature in M4 (ADR 0009). alpha_ticks = 0 is plain A-S.
- **Risk:** hard position limit (worst case including in-flight orders), quoting window 09:35 to
  15:50, then cancel and flatten with IOC orders at the far touch.
- **P&L decomposition:** spread capture (mid at fill vs price), adverse selection (mid 10 s after
  vs at fill), inventory/other (remainder, including flattening), and fees/rebates.
- **Evaluation:** parameters chosen on the first ITCH days, reported on later held-out days.

## Consequences
- The no-impact assumption of ADR 0006 still holds: our orders never change the real queue, so
  results are optimistic for sizes that would be a visible part of the touch. The strategy uses
  100 shares to stay small relative to these names' touch sizes.
- A fill-model calibration against real fills is impossible without trading; M6 compares
  simulated fills against a live book instead.

## Alternatives considered
- **Gueant-Lehalle-Fernandez-Tapia closed form:** similar shape; A-S is the standard baseline.
- **PPO policy:** stretch goal in the plan; deferred until the baseline is understood.
