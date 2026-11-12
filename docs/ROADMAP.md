# Roadmap

Full plan: project-plans/quant-trading/PLAN.md (project files). Concept A, ADR 0001.

| # | Milestone | Status |
|---|---|---|
| M0 | Scaffold, CI, ADRs, data scripts | done |
| M1 | L3 order book + ITCH 5.0 replay, Python reference, benchmark | done (real ITCH day 2019-01-30 passes) |
| M2 | Matching + backtester (queue fills, latency, fees, determinism) | done |
| M3 | Crypto L2 data path (recorder, dumps, L2 mode) | done (24 h recording run pending) |
| M4 | Alpha research (features, purged walk-forward, deflated Sharpe) | done (signal real, IC 0.13 @10s; does not survive costs) |
| M5 | Market making (Avellaneda-Stoikov, alpha skew, risk, calibration) | done (loses to adverse selection on held-out days; calibration vs real fills not possible) |
| M6 | Live paper trading on public crypto feed | |
| M7 | FPGA ITCH feed handler + book in SystemVerilog (sim, bit-exact) | done (sim; synthesis in M8) |
| M8 | FPGA bring-up (optional) | |
| M9 | Write-up + audit | |
