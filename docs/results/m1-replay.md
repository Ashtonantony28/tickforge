# M1 results: order book + ITCH 5.0 replay

Measured 2026-11-12 in the development container (4 vCPU cloud VM, GCC 13.3, `-O3` Release, no
`-march=native`). Numbers on a desktop CPU will differ; rerun with the commands below.

## Correctness (bit-exact vs Python reference book)

| Stream | Messages | Live orders at end | C++ digest | Python digest |
|---|---|---|---|---|
| `fixtures/synth_small.itch` (seed 7, 6 stocks) | 20,000 | see expected.json | `2173f5ec0b89cf9c` | `2173f5ec0b89cf9c` |
| `fixtures/synth_faults.itch` (seed 11, 4% faults) | 8,000 | 827 | `76edf75caba8d5f6` | `76edf75caba8d5f6` |
| synthetic, seed 42, 50 stocks | 2,000,000 | 186,941 | `704f0f1b17f93435` | `704f0f1b17f93435` |

The digest folds the top of book of the touched instrument after every book-changing message, so a
match means the two implementations agree after each of the 2M updates, not just at the end. pytest
additionally compares top-5 depth and FIFO queues for every instrument on 14 more random streams.

## Throughput

| Mode | Best | Median (5 runs) |
|---|---|---|
| Replay + per-message digest | 12.6M msgs/s | 12.1M msgs/s |
| Replay only (`--no-digest`) | 13.5M msgs/s | 12.1M msgs/s |

Plan target (>= 10M msgs/s single-threaded) is met on synthetic data.

## Reproduce

```bash
cmake -S . -B build -G Ninja && cmake --build build
PYTHONPATH=python python3 -m tickforge.synth /tmp/bench.itch -n 2000000 --seed 42 --stocks 50
./build/engine/tf_bench /tmp/bench.itch --runs 5
./build/engine/tf_replay /tmp/bench.itch
```

## Real ITCH day: 2019-01-30 (passes)
Nasdaq sample file `01302019.NASDAQ_ITCH50.gz` (4.76 GB, SHA-256 pinned in `data/checksums.json`),
replayed in the cloud container on 2026-11-12, streamed through `gzip -dc` (no uncompressed copy):

| Messages | Adds | Executions | Cancels | Deletes | Replaces | Trades (hidden) | unknown_ref | overfill | bad_messages | Live orders at end |
|---|---|---|---|---|---|---|---|---|---|---|
| 368,366,634 | 164,696,353 | 8,255,881 | 4,669,874 | 158,273,361 | 27,222,746 | 1,326,184 | 0 | 0 | 0 | 0 |

Digest `33693a0ece28018e`. Pass criteria (ADR 0004) met: `unknown_ref == 0`, `overfill == 0`,
`bad_messages == 0`; every order added during the day is gone by the close. Wall time 170 s
including decompression (gzip-bound). From the uncompressed 11.2 GB file, with the per-message
digest on: 79.2 s and 77.6 s, i.e. **4.7M msgs/s** on real data. That is below the 12M msgs/s
synthetic number: a real day has ~12k instruments and a much larger live-order set, so the order
index and level vectors miss cache far more often. Profiling this is an M9 item.
```bash
python data/fetch.py itch-sample --date 01302019
gzip -dc data/raw/itch/01302019.NASDAQ_ITCH50.gz | ./build/engine/tf_replay /dev/stdin
```
