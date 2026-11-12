"""Research hygiene (M4, ADR 0009): purged CV with embargo, deflated Sharpe, PBO, IC.

References: López de Prado, *Advances in Financial Machine Learning* (2018) ch. 7 (purging,
embargo) and ch. 11-14; Bailey & López de Prado, "The Deflated Sharpe Ratio" (2014); Bailey,
Borwein, López de Prado, Zhu, "The Probability of Backtest Overfitting" (2017).
"""
from __future__ import annotations

from itertools import combinations
from math import e, sqrt

import numpy as np
from scipy import stats

EULER_GAMMA = 0.5772156649015329


def purged_kfold(t0: np.ndarray, t1: np.ndarray, k: int, embargo: int = 0):
    """Yield (train_idx, test_idx) for k contiguous test folds over samples sorted by t0.

    Sample i's label spans [t0[i], t1[i]]. A training sample is purged if its span overlaps the
    test fold's span [min t0, max t1], and embargoed if it starts within `embargo` time units
    after the fold ends (serial correlation leaking backwards through features).
    """
    n = len(t0)
    if np.any(np.diff(t0) < 0):
        raise ValueError("samples must be sorted by t0")
    bounds = np.linspace(0, n, k + 1).astype(int)
    for a, b in zip(bounds[:-1], bounds[1:]):
        test = np.arange(a, b)
        lo, hi = t0[a], t1[a:b].max()
        overlap = (t0 <= hi) & (t1 >= lo)
        emb = (t0 > hi) & (t0 <= hi + embargo)
        train = np.where(~overlap & ~emb)[0]
        yield train, test


def sharpe(r: np.ndarray) -> float:
    r = np.asarray(r, dtype=float)
    sd = r.std(ddof=1)
    return float(r.mean() / sd) if sd > 0 else 0.0


def deflated_sharpe(r: np.ndarray, sr_trials: np.ndarray) -> dict:
    """Probability that the true (per-period) Sharpe of `r` exceeds the best of N noise trials.

    `sr_trials` are the per-period Sharpe ratios of *every* configuration tried (including this
    one), which is what makes the deflation honest: more trials -> higher bar.
    """
    r = np.asarray(r, dtype=float)
    T = len(r)
    sr = sharpe(r)
    N = max(len(sr_trials), 1)
    var_sr = float(np.var(sr_trials, ddof=1)) if N > 1 else 0.0
    if N > 1:
        z = (1 - EULER_GAMMA) * stats.norm.ppf(1 - 1 / N) + EULER_GAMMA * stats.norm.ppf(1 - 1 / (N * e))
        sr0 = sqrt(var_sr) * z
    else:
        sr0 = 0.0
    g3 = float(stats.skew(r))
    g4 = float(stats.kurtosis(r, fisher=False))
    denom = sqrt(max(1 - g3 * sr + (g4 - 1) / 4 * sr * sr, 1e-12))
    dsr = float(stats.norm.cdf((sr - sr0) * sqrt(T - 1) / denom))
    return {"sr": sr, "sr0": sr0, "dsr": dsr, "n_trials": N, "T": T, "skew": g3, "kurt": g4}


def pbo(M: np.ndarray, n_blocks: int = 10) -> dict:
    """Combinatorially symmetric cross-validation. M: [T periods, N configs] of returns.

    For every way of choosing half the time blocks as in-sample, pick the config with the best IS
    Sharpe and record its OOS rank. PBO = share of splits where that config is below the OOS median.
    """
    T, N = M.shape
    blocks = np.array_split(np.arange(T), n_blocks)
    logits = []
    for is_blocks in combinations(range(n_blocks), n_blocks // 2):
        is_idx = np.concatenate([blocks[i] for i in is_blocks])
        oos_idx = np.concatenate([blocks[i] for i in range(n_blocks) if i not in is_blocks])
        sr_is = np.array([sharpe(M[is_idx, j]) for j in range(N)])
        sr_oos = np.array([sharpe(M[oos_idx, j]) for j in range(N)])
        best = int(np.argmax(sr_is))
        rank = (stats.rankdata(sr_oos)[best]) / (N + 1)
        logits.append(np.log(rank / (1 - rank)))
    logits = np.array(logits)
    return {"pbo": float((logits <= 0).mean()), "splits": len(logits), "median_logit": float(np.median(logits))}


def spearman_ic(pred: np.ndarray, y: np.ndarray) -> float:
    m = np.isfinite(pred) & np.isfinite(y)
    if m.sum() < 10:
        return float("nan")
    return float(stats.spearmanr(pred[m], y[m]).statistic)
