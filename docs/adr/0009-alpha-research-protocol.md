# 0009: Alpha research protocol: day-level walk-forward, every trial counted

- **Status:** Accepted
- **Date:** 2026-11-12

## Context
M4 asks whether order-book features predict short-horizon returns well enough to trade. Short
samples of high-frequency data make it easy to fool yourself: overlapping labels leak through
shuffled CV, and trying many signals inflates the best Sharpe. CLAUDE.md rule 3 requires costs,
an out-of-sample split and the deflated Sharpe next to every headline Sharpe.

## Decision
- **Data:** Nasdaq TotalView-ITCH sample days (the only free full-depth equity data), 15 liquid
  Nasdaq names, sampled on a 1 s grid from 09:35 to 15:55 by `tf_features` (C++, causal raw
  counters). All windowed features and labels are computed in Python from those counters.
- **Labels:** forward log-mid return over 10 s and 60 s, in bps. Labels never cross a day.
- **Split:** expanding walk-forward by day (train on days < k, test day k, at least two training
  days). Hyperparameters are fixed in code before the first run, not tuned on test days.
- **Leakage check:** on training days, shuffled K-fold vs purged K-fold with embargo (Lopez de
  Prado ch. 7) is reported for the ridge model; `purged_kfold` is unit-tested to never train on a
  label window that overlaps the test window.
- **Trials:** every single feature (sign fixed on training data), ridge and gradient boosting, for
  each horizon. All trials appear in `docs/results/graveyard.md`; the deflated Sharpe uses the
  variance of all trials' Sharpes, and PBO (CSCV) is computed across them.
- **Monetisation proxy:** cross the spread when |prediction| exceeds the training 90th
  percentile, exit after h at the far touch, pay taker fees both ways. A signal "survives" only
  with DSR >= 0.95 and positive net P&L.

## Consequences
- Seven sample days (2019) is a small sample: per-day IC is reported so the reader can see the
  dispersion. Conclusions are about these days and names only.
- The taker proxy is a hard test; signals that fail it can still be useful as a quote skew for
  a passive strategy, which is what M5 tests.

## Alternatives considered
- **LOBSTER samples:** one day per stock at a time, fewer names; ITCH covers all names at once.
- **DeepLOB-style CNN:** deferred; with seven days, a large model would mostly measure variance.
