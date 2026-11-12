# M6 results: live paper trading on Binance BTCUSDT

Design: ADR 0011. Strategy: Avellaneda-Stoikov on the synced L2 book (gamma 0.001 per lot, k 5 per
USDT, tau 60 s, 0.001 BTC quotes, max 0.01 BTC, join-only, 200 ms requote limit, 50 ms simulated
latency). Simulated fills only; maker fee set to 0 so the strategy's edge is visible (Binance VIP0
spot maker fee is 10 bps, which alone would cost ~0.10 USDT per fill here).

## Run 1: 2026-11-12, 1.23 h live (container restart ended it)

| Metric | Value |
|---|---|
| Feed | 44,214 depth diffs, 14 snapshot checks, 0 mismatches, 1 gap (websocket drop at 07:39 UTC) |
| Fills | 1,345 x 0.001 BTC (~98k USDT notional) |
| P&L | -9.24 USDT marked to mid at the end (~ -0.9 bps of notional), position -0.0069 BTC |
| Log | 6,920 hash-chained lines, chain verifies |
| Live vs replay | **identical**: replaying the recorded feed through the code version that ran live reproduces all 6,917 replayable log lines byte for byte. The live log has 3 more lines because the last few seconds of the recording were in an unflushed gzip block when the container was killed. |

With the current code (reconnects no longer forced into gaps, ADR 0011), replay diverges at line
5,617: that is exactly the moment the old live engine pulled its quotes on the reconnect. This is
the check working as intended: a strategy change shows up as a log difference.

Like M5 on equities, the market maker loses to adverse selection here, and with the real 10 bps maker
fee it would lose far more. That is the honest result of the baseline.

## Not done
- **Two-week run:** the plan's M6 verify asks for a 2-week log. The cloud container restarted
  about every 1 to 2 hours today, so this needs a host that stays up. Command below.
- **Dashboard:** not built; `paper.log` marks (one per minute) hold the equity curve.

## Reproduce
```bash
pip install websockets
PYTHONPATH=python python -m tickforge.paper run --out data/raw/paper/run1 --hours 336
PYTHONPATH=python python -m tickforge.paper verify data/raw/paper/run1     # exit 0 = chain ok and replay identical
pytest -q tests/test_paper.py
```
