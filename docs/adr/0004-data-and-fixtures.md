# 0004: Real data is downloaded locally; CI uses synthetic ITCH fixtures

- **Status:** Accepted
- **Date:** 2026-11-12

## Context
Nasdaq ITCH sample days are several GB compressed and carry sample-data terms; the development
container cannot reach the data hosts at all. CI must still prove book reconstruction bit-exact.

## Decision
- `data/fetch.py` downloads named sources and verifies SHA-256 against `data/checksums.json`
  (pinned on first successful download, then reviewed).
- A seeded synthetic ITCH 5.0 generator (`python/tickforge/synth.py`) writes valid binary ITCH with
  realistic order lifecycles. A small fixture plus its expected digest is committed; CI checks the C++
  engine and the Python reference book against it, and against larger freshly generated streams.
- ITCH does not publish book snapshots, so "zero mismatches on a real day" is measured as: zero
  unknown-reference and zero over-fill events across the full day, and C++ == Python reference digest.

## Consequences
Real-day results are produced on a machine with network access and recorded in `docs/results/`.
