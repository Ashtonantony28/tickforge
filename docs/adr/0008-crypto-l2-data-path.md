# 0008: Crypto L2 data path: raw recording, offline sync, fixed-point L2 book

- **Status:** Accepted
- **Date:** 2026-11-12

## Context
M3 adds a second market: crypto spot, where venues publish aggregated price levels (L2), not
orders (L3). The plan's verify is "recorder runs 24h without gaps; replay reproduces recorded
snapshots". From the development cloud, `api.binance.com` / `stream.binance.com` answer HTTP 451
(restricted location), but Binance's market-data-only hosts `data-api.binance.vision` (REST) and
`data-stream.binance.vision` (websocket) and the dump site `data.binance.vision` work.

## Decision
- **Venue:** Binance spot via the `*.binance.vision` market-data hosts. They accept no keys and no
  orders, which also enforces CLAUDE.md rule 4 at the network level.
- **Record raw, sync offline.** The recorder stores every websocket payload untouched with a local
  receive timestamp, plus a REST depth snapshot (1000 levels) at start, every 5 minutes, after a
  live gap and at shutdown. All parsing and sequence logic lives in `tickforge.l2.BinanceSync`, so
  a bug there never corrupts a recording, and recordings can be re-processed.
- **Sync** follows Binance's documented procedure (drop `u <= L`, first diff straddles `L+1`, then
  `U == prev_u + 1`). A break is a gap: the book is unsynced until the next snapshot.
- **Every later snapshot is a check.** The book is rewound with an undo log to update id `L` and
  compared level-by-level inside the band both sides know (the snapshot's depth and the depth of
  the snapshot the book was loaded from). If the diff covering `L` straddles it, that diff's prices
  are excluded and the check is counted as non-exact.
- **Exact numbers.** Prices and quantities are int64 at 1e-8, parsed from decimal strings
  (`parse_dec`); floats never touch book state.
- **Engine L2 mode:** `tf::L2Book` stores one sorted vector per side with the best level at the
  back; `tf::L2Replayer` consumes a 32-byte event stream (Clear / Bid / Ask / Batch) emitted by the
  sync stage and folds a top-of-book digest per batch, bit-exact with `tickforge.l2.L2Replayer`.

## Consequences
- The recorder is ~200 lines with no venue logic to get wrong live; the checker can be improved
  and re-run on old recordings.
- Checks are only as deep as the snapshot band; levels far from the touch that the book never saw
  are not verified (they are not traded against in M5 either).
- Recording quality depends on the host staying up; a 24h run needs a machine that is not
  reclaimed (cloud container or the user's PC).

## Alternatives considered
- **Coinbase / Kraken.** Coinbase's full `level2` channel now requires authentication; Kraken's
  public book has a CRC32 checksum (a nice check) but thinner BTC liquidity. Kept as follow-ups.
- **Sync inside the recorder.** Simpler to describe, but a sync bug would silently lose data.
- **std::map L2 book.** Allocates per insert; violates the no-heap hot-path rule.
