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

## Real ITCH day (pending)
The container cannot reach Nasdaq's sample-data host. On a machine with network access:
```bash
python data/fetch.py itch-sample --date 01302019 --pin
gunzip -k data/raw/itch/01302019.NASDAQ_ITCH50.gz
./build/engine/tf_replay data/raw/itch/01302019.NASDAQ_ITCH50
```
Pass criteria (ADR 0004): `unknown_ref == 0`, `overfill == 0`, `bad_messages == 0`.
