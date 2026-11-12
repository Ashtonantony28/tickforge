# M2 results: backtester

Model: ADR 0006 (exact L3 queue position, latency on every action, maker/taker fees, no impact).

## Correctness evidence

| Check | Where |
|---|---|
| Passive fill only after the exact shares ahead are consumed; order behind us executing fills us first | `tests/test_backtester.py::test_passive_fill_uses_exact_queue_position` |
| Cancel/delete of orders ahead reduces queue without a fill | `test_order_ahead_cancelled_reduces_ahead_without_fill` |
| Aggressive IOC walks visible levels, taker fee, remainder dropped | `test_aggressive_ioc_walks_visible_levels_with_taker_fee` |
| Trade-through and opposite-touch fills | `test_trade_through_fills_resting_order_fully`, `test_opposite_side_reaching_our_price_fills_us` |
| Cancel racing a fill is rejected because of latency | `test_cancel_racing_a_fill_is_rejected_by_latency` |
| Post-only rejected if marketable on arrival | `test_post_only_rejected_when_marketable_on_arrival` |
| Worst-case per-side position limit | `test_risk_limit_rejects_orders_that_could_breach_position` |
| Cash/position equal the fill log exactly | `test_touch_quoter_trades_and_accounting_identity`, `engine/tests/test_sim.cpp` |
| Same seed -> bit-identical fills and P&L samples; different jitter seed -> different | `test_backtest_is_bit_reproducible` |
| No look-ahead: changing data after time T cannot change any fill before T | `test_no_lookahead_future_data_cannot_change_past_fills` |

Every scenario's expected fills, cash and fees were computed by hand in the test.

## Baseline run (synthetic data, illustrative only)

`tf_backtest` with the TouchQuoter baseline (join both touches, 100 shares, post-only, max 1000
shares inventory) on the 2M-message synthetic stream, instrument 1:

| Latency | Fills | Bought / sold | Final position | Gross P&L | Rebates | Net |
|---|---|---|---|---|---|---|
| 1 µs | 133 | 4,901 / 5,778 | -877 | $285.13 | $21.36 | $306.48 |
| 200 µs | 140 | 5,201 / 6,078 | -877 | $285.13 | $22.56 | $307.68 |

**These numbers say nothing about real profitability.** The synthetic generator has no informed
flow, so there is no adverse selection, and P&L is dominated by the random-walk mark on a
-877 share inventory. Real-data results and the spread-capture / adverse-selection split come in M5.

## Reproduce
```bash
cmake --build build
./build/engine/tf_backtest /tmp/bench.itch --locate 1 --latency-ns 20000
pytest -q tests/test_backtester.py
```
