# Tickforge

A limit-order-book research stack: a C++ engine that rebuilds exchange order books from Nasdaq
TotalView-ITCH 5.0, a backtester with queue-position fills (M2), ML alpha research on microstructure
signals (M4), market making (M5), live paper trading (M6), and an FPGA feed handler (M7) checked
bit-exact against the C++ engine.

**Backtesting and paper trading only.** There is no order-submission client for any real venue.

## Status

| Milestone | State |
|---|---|
| M0 scaffold, CI, ADRs, data scripts | done |
| M1 L3 order book + ITCH replay | done: bit-exact vs Python reference, 12M msgs/s ([results](docs/results/m1-replay.md)) |
| M2 backtester | done: exact L3 queue position, latency, fees, risk limits, reproducible ([results](docs/results/m2-backtester.md)) |
| M7 RTL feed handler | done in simulation: bit-exact vs C++ engine, 1 byte/cycle, 4-cycle median latency ([results](docs/results/m7-rtl.md)) |
| M3-M6, M8, M9 | see [roadmap](docs/ROADMAP.md) |

## Quick start

```bash
pip install -r requirements-dev.txt
cmake -S . -B build -G Ninja && cmake --build build
ctest --test-dir build --output-on-failure    # C++ unit + golden fixture tests
pytest -q                                      # C++ vs Python reference, backtester, RTL vs C++ (needs verilator+cocotb)
```

## Layout

```
engine/include/tickforge/  itch.hpp · order_book.hpp · flat_map.hpp · replay.hpp · digest.hpp · sim.hpp (backtester)
engine/tests, tools, bench C++ tests, tf_replay, tf_backtest, tf_bench
bindings/                  pybind11 module _tickforge
python/tickforge/          itch codec, reference book, synthetic ITCH generator
rtl/, verif/               SystemVerilog feed handler + cocotb bench (Verilator)
fixtures/                  committed synthetic ITCH fixtures + expected results
data/                      download scripts + checksums (data itself is never committed)
docs/                      ADRs, roadmap, data sources, results
```

Conventions are in [CLAUDE.md](CLAUDE.md).
