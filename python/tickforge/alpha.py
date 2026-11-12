"""M4 alpha study on real Nasdaq ITCH days: walk-forward by day, every trial logged (ADR 0009).

    python -m tickforge.alpha data/cache/features --out docs/results

Protocol (fixed before looking at test days):
  * Samples: 1-second grid, 09:35-15:55, 15 liquid Nasdaq names, valid uncrossed book.
  * Labels: forward log-mid return over h in {10, 60} s, in bps.
  * Walk-forward: test day k is predicted by models fit only on days < k (expanding window,
    at least 2 training days). Labels never cross a day boundary, so day-level splits need no
    purge; purged k-fold with embargo is used inside training days for the leakage check.
  * Trials: each single feature (sign fixed on training days), ridge on all features, gradient
    boosting on all features, for each horizon. Every trial is reported (graveyard included) and
    counts toward the deflated Sharpe.
  * Trading proxy: every h seconds per symbol, if |prediction| is above the training 90th
    percentile, cross the spread in the predicted direction and exit after h seconds at the
    then-prevailing far touch. Cost = entry half-spread + exit half-spread + taker fee both ways.
    This is deliberately the simplest way to monetise a signal; M5 tries a passive way.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from .features import FEATURES, forward_return_bps, load_grid, make_features
from .research import deflated_sharpe, pbo, purged_kfold, sharpe, spearman_ic

HORIZONS = (10, 60)
SKIP = 300  # seconds trimmed at open and close
TAKER_FEE = 0.0030  # $/share per side (Nasdaq-like); ADR 0006 uses the same figure
MIN_TRAIN_DAYS = 2
Q = 0.90


def day_key(p: Path) -> str:
    s = p.stem  # MMDDYYYY
    return s[4:] + s[:4]


def load_day(p: Path):
    g = load_grid(p)
    X, mid, valid = make_features(g)
    bp, ap = g.col("bid_px") / 1e4, g.col("ask_px") / 1e4
    T = X.shape[0]
    keep = np.zeros(T, bool)
    keep[SKIP:T - SKIP] = True
    out = {"X": X, "mid": mid, "bid": bp, "ask": ap, "valid": valid & keep[:, None], "symbols": g.symbols}
    for h in HORIZONS:
        out[f"y{h}"] = forward_return_bps(mid, h)
    return out


def flatten(d, h, every=1, offset=0):
    """Rows (t, s) with finite features and label; returns X, y, t, s."""
    X, y, v = d["X"], d[f"y{h}"], d["valid"]
    T = X.shape[0]
    tmask = np.zeros(T, bool)
    tmask[offset::every] = True
    m = v & tmask[:, None] & np.isfinite(y) & np.isfinite(X).all(axis=-1)
    t_idx, s_idx = np.nonzero(m)
    return X[t_idx, s_idx], y[t_idx, s_idx], t_idx, s_idx


class Ridge:
    def __init__(self, lam=10.0):
        self.lam = lam

    def fit(self, X, y):
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-12
        Z = (X - self.mu) / self.sd
        A = Z.T @ Z + self.lam * np.eye(Z.shape[1])
        self.w = np.linalg.solve(A, Z.T @ (y - y.mean()))
        self.b = y.mean()
        return self

    def predict(self, X):
        return ((X - self.mu) / self.sd) @ self.w + self.b


class Single:
    def __init__(self, j):
        self.j = j

    def fit(self, X, y):
        self.sign = 1.0 if spearman_ic(X[:, self.j], y) >= 0 else -1.0
        return self

    def predict(self, X):
        return self.sign * X[:, self.j]


def make_gbm():
    from sklearn.ensemble import HistGradientBoostingRegressor

    return HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, max_leaf_nodes=31,
                                         min_samples_leaf=500, l2_regularization=1.0, random_state=0)


def trials():
    for h in HORIZONS:
        for j, f in enumerate(FEATURES):
            yield f"{f}@{h}s", h, (lambda j=j: Single(j))
        yield f"ridge@{h}s", h, (lambda: Ridge())
        yield f"gbm@{h}s", h, make_gbm


def trade_proxy(d, h, model, thr):
    """Per-symbol decisions every h seconds; returns (minute index, gross bps, net bps) arrays."""
    X, y, t, s = flatten(d, h, every=h)
    if len(y) == 0:
        return np.array([]), np.array([]), np.array([])
    p = model.predict(X)
    go = np.abs(p) > thr
    t, s, p, y = t[go], s[go], p[go], y[go]
    side = np.sign(p)
    mid0 = d["mid"][t, s]
    half0 = (d["ask"][t, s] - d["bid"][t, s]) / 2
    half1 = (d["ask"][t + h, s] - d["bid"][t + h, s]) / 2
    gross = side * y
    cost = (half0 + half1 + 2 * TAKER_FEE) / mid0 * 1e4
    return t // 60, gross, gross - cost


def per_minute(minutes, pnl, n_min):
    out = np.zeros(n_min)
    np.add.at(out, minutes.astype(int), pnl)
    return out


def leakage_check(train_days, h):
    """IC of ridge under shuffled K-fold vs purged K-fold (embargo h) on the training days."""
    Xs, ys, ts = [], [], []
    off = 0
    for d in train_days:
        X, y, t, _ = flatten(d, h, every=5)
        Xs.append(X), ys.append(y), ts.append(t + off)
        off += d["X"].shape[0] + 10 * h
    X, y, t = np.concatenate(Xs), np.concatenate(ys), np.concatenate(ts).astype(float)
    order = np.argsort(t, kind="stable")
    X, y, t = X[order], y[order], t[order]
    rng = np.random.default_rng(0)
    perm = rng.permutation(len(y))
    folds = np.array_split(perm, 5)
    ic_shuf = []
    for f in folds:
        tr = np.setdiff1d(perm, f)
        ic_shuf.append(spearman_ic(Ridge().fit(X[tr], y[tr]).predict(X[f]), y[f]))
    ic_purged = [spearman_ic(Ridge().fit(X[tr], y[tr]).predict(X[te]), y[te])
                 for tr, te in purged_kfold(t, t + h, 5, embargo=h)]
    return float(np.mean(ic_shuf)), float(np.mean(ic_purged))


def run(feature_dir: Path, out_dir: Path) -> dict:
    t_start = time.time()
    paths = sorted(feature_dir.glob("*.bin"), key=day_key)
    days = [load_day(p) for p in paths]
    names = [day_key(p) for p in paths]
    n_min = days[0]["X"].shape[0] // 60 + 1
    results = {}
    minute_net = {}
    for name, h, factory in trials():
        ics, gross_all, net_all, n_trades, series = [], [], [], 0, []
        for k in range(MIN_TRAIN_DAYS, len(days)):
            tr = [flatten(d, h, every=2) for d in days[:k]]
            Xtr = np.concatenate([a[0] for a in tr])
            ytr = np.concatenate([a[1] for a in tr])
            model = factory().fit(Xtr, ytr)
            thr = np.quantile(np.abs(model.predict(Xtr[:: 7])), Q)
            Xte, yte, _, _ = flatten(days[k], h)
            ics.append(spearman_ic(model.predict(Xte), yte))
            mins, gross, net = trade_proxy(days[k], h, model, thr)
            gross_all.append(gross), net_all.append(net)
            n_trades += len(net)
            series.append(per_minute(mins, net, n_min)[SKIP // 60 + 1:-(SKIP // 60 + 1)])
        gross_c, net_c = np.concatenate(gross_all), np.concatenate(net_all)
        minute_net[name] = np.concatenate(series)
        results[name] = {
            "horizon_s": h, "ic_by_day": [round(x, 4) for x in ics], "ic_mean": round(float(np.mean(ics)), 4),
            "trades": n_trades, "gross_bps_per_trade": round(float(gross_c.mean()), 3) if n_trades else 0.0,
            "net_bps_per_trade": round(float(net_c.mean()), 3) if n_trades else 0.0,
        }
    M = np.stack([minute_net[k] for k in results], axis=1)
    sr_all = np.array([sharpe(M[:, j]) for j in range(M.shape[1])])
    for j, k in enumerate(results):
        ds = deflated_sharpe(M[:, j], sr_all)
        results[k]["sharpe_ann"] = round(ds["sr"] * np.sqrt(390 * 252), 2)
        results[k]["dsr"] = round(ds["dsr"], 4)
    best = max(results, key=lambda k: results[k]["sharpe_ann"])
    leak = {f"{h}s": leakage_check(days[:MIN_TRAIN_DAYS], h) for h in HORIZONS}
    summary = {
        "days": names, "test_days": names[MIN_TRAIN_DAYS:], "symbols": days[0]["symbols"],
        "n_trials": len(results), "pbo": pbo(M, n_blocks=10), "best_by_sharpe": best,
        "leakage_ic_shuffled_vs_purged": leak, "trials": results,
        "runtime_s": round(time.time() - t_start, 1),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "m4-alpha.json").write_text(json.dumps(summary, indent=2) + "\n")
    (out_dir / "graveyard.md").write_text(graveyard_md(summary))
    return summary


def graveyard_md(s: dict) -> str:
    """Every trial, sorted by out-of-sample IC; a trial 'survives' only with DSR >= 0.95 and net > 0."""
    lines = ["# Signal graveyard (generated by `make report-alpha`; do not edit by hand)", "",
             f"Walk-forward test days: {', '.join(s['test_days'])}. Trials: {s['n_trials']}. "
             f"PBO across trials: {s['pbo']['pbo']:.2f}.", "",
             "| Trial | OOS IC (mean) | IC by test day | Trades | Gross bps/trade | Net bps/trade | Ann. Sharpe (net) | DSR | Status |",
             "|---|---|---|---|---|---|---|---|---|"]
    for k, v in sorted(s["trials"].items(), key=lambda kv: -kv[1]["ic_mean"]):
        alive = v["dsr"] >= 0.95 and v["net_bps_per_trade"] > 0
        lines.append(f"| `{k}` | {v['ic_mean']:+.4f} | {' '.join(f'{x:+.3f}' for x in v['ic_by_day'])} | "
                     f"{v['trades']:,} | {v['gross_bps_per_trade']:+.2f} | {v['net_bps_per_trade']:+.2f} | "
                     f"{v['sharpe_ann']:+.2f} | {v['dsr']:.3f} | {'survives' if alive else 'graveyard'} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("feature_dir", type=Path)
    p.add_argument("--out", type=Path, default=Path("docs/results"))
    a = p.parse_args(argv)
    s = run(a.feature_dir, a.out)
    print(json.dumps({k: v for k, v in s.items() if k != "trials"}, indent=2))
    for k, v in sorted(s["trials"].items(), key=lambda kv: -kv[1]["ic_mean"]):
        print(f"{k:22s} IC {v['ic_mean']:+.4f}  trades {v['trades']:6d}  gross {v['gross_bps_per_trade']:+7.3f}"
              f"  net {v['net_bps_per_trade']:+7.3f}  SR {v['sharpe_ann']:+6.2f}  DSR {v['dsr']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
