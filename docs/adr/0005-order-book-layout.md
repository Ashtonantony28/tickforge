# 0005: Order book layout: pooled orders, flat hash index, sorted level vectors with touch at the back

- **Status:** Accepted
- **Date:** 2026-11-12

## Context
Replay must sustain >= 10M ITCH messages/s single-threaded, and the backtester (M2) needs queue
position (FIFO order within a level). Most ITCH activity is at or near the touch.

## Decision
- Orders in one pool (`std::vector<Order>` + free list); no per-order heap allocation.
- `FlatMap64`: open addressing, linear probing, backward-shift deletion, load <= 1/2, keyed by order ref.
- Each side is a `std::vector<Level>` sorted worst -> best, so the best level is `back()`. Lookup checks
  the touch first, then binary-searches. Inserting/removing a level near the touch shifts few elements.
- Each level holds an intrusive doubly linked FIFO of its orders (prev/next indices in the pool).
- Orders store their price, not a level index (indices shift when levels are inserted/erased).

## Consequences
O(log L) level lookup (L = levels on a side), O(1) order lookup, cache-friendly scans of top-N.
Deep-book inserts far from the touch cost a vector shift; measured throughput is well above target on
synthetic data (see `docs/results/m1-replay.md`); revisit with a ladder array if real days disagree.

## Alternatives considered
`std::map<price, Level>` (pointer chasing, ~3-5x slower in typical benchmarks); fixed price ladder
array (fastest, but needs per-instrument price-range management and recentering).
