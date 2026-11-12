"""M4 research hygiene: causal features, aligned labels, purged CV, deflated Sharpe, PBO."""
from __future__ import annotations

import json

import numpy as np
import pytest

from tickforge.features import FEATURES, Grid, forward_return_bps, load_grid, make_features
from tickforge.research import deflated_sharpe, pbo, purged_kfold, sharpe

FIELDS = ["bid_px", "bid_qty", "ask_px", "ask_qty", "bid_depth5", "ask_depth5", "bid_count1", "ask_count1",
          "ofi", "buy_vol", "sell_vol", "trades", "add_bid", "add_ask", "cxl_bid", "cxl_ask", "last_px"]


def random_grid(T=900, S=3, seed=0) -> Grid:
    rng = np.random.default_rng(seed)
    mid = 1_000_000 + np.cumsum(rng.integers(-1, 2, (T, S)) * 100, axis=0)
    spread = rng.integers(1, 4, (T, S)) * 100
    d = np.zeros((T, S, len(FIELDS)), np.int64)
    d[..., 0], d[..., 2] = mid - spread // 2 // 100 * 100, mid - spread // 2 // 100 * 100 + spread
    d[..., 1], d[..., 3] = rng.integers(100, 5000, (T, S)), rng.integers(100, 5000, (T, S))
    d[..., 4], d[..., 5] = d[..., 1] * 4, d[..., 3] * 4
    d[..., 6], d[..., 7] = rng.integers(1, 9, (T, S)), rng.integers(1, 9, (T, S))
    for k in range(8, 16):
        d[..., k] = np.cumsum(rng.integers(0, 1000, (T, S)), axis=0)
    d[..., 16] = mid
    return Grid(["A", "B", "C"][:S], FIELDS, 0, 10**9, d)


def test_features_are_causal():
    g = random_grid()
    X, mid, _ = make_features(g)
    cut = 500
    g2 = Grid(g.symbols, g.fields, 0, 10**9, g.data.copy())
    g2.data[cut + 1:] = np.random.default_rng(9).permutation(g2.data[cut + 1:])  # scramble the future
    X2, mid2, _ = make_features(g2)
    np.testing.assert_array_equal(X[: cut + 1], X2[: cut + 1])  # NaNs compare equal
    assert X.shape[-1] == len(FEATURES)


def test_label_is_strictly_forward():
    mid = np.array([[100.0], [101.0], [99.0], [99.0]])
    y = forward_return_bps(mid, 1)
    assert y[0, 0] == pytest.approx(np.log(101 / 100) * 1e4)
    assert np.isnan(y[-1, 0])


def test_grid_roundtrip(tmp_path):
    g = random_grid(T=10)
    p = tmp_path / "g.bin"
    hdr = json.dumps({"symbols": g.symbols, "fields": FIELDS, "start_ns": 5, "step_ns": 7})
    p.write_bytes(hdr.encode() + b"\n" + g.data.astype("<i8").tobytes())
    g2 = load_grid(p)
    np.testing.assert_array_equal(g2.data, g.data)
    assert g2.start_ns == 5 and g2.symbols == g.symbols


def test_purged_kfold_never_trains_on_overlapping_labels():
    t0 = np.arange(1000, dtype=float)
    t1 = t0 + 10
    for train, test in purged_kfold(t0, t1, 5, embargo=20):
        lo, hi = t0[test].min(), t1[test].max()
        assert not np.any((t0[train] <= hi) & (t1[train] >= lo))
        assert not np.any((t0[train] > hi) & (t0[train] <= hi + 20))
        assert len(train) > 0


def test_purging_removes_leak_that_shuffled_cv_rewards():
    # Label = sum of the next 50 shocks; feature = sum of the *previous* 50. Overlapping labels make
    # neighbours nearly identical, so a model that memorises neighbours looks good under shuffled CV.
    from sklearn.neighbors import KNeighborsRegressor

    rng = np.random.default_rng(1)
    e = rng.standard_normal(3000)
    c = np.concatenate([[0], np.cumsum(e)])
    idx = np.arange(50, 2900)
    y = c[idx + 50] - c[idx]
    X = np.stack([idx.astype(float)], axis=1)  # time itself: pure memorisation feature
    knn = lambda: KNeighborsRegressor(n_neighbors=3)
    perm = rng.permutation(len(y))
    shuf = []
    for f in np.array_split(perm, 5):
        tr = np.setdiff1d(perm, f)
        shuf.append(np.corrcoef(knn().fit(X[tr], y[tr]).predict(X[f]), y[f])[0, 1])
    t0 = idx.astype(float)
    purged = [np.corrcoef(knn().fit(X[tr], y[tr]).predict(X[te]), y[te])[0, 1]
              for tr, te in purged_kfold(t0, t0 + 50, 5, embargo=50)]
    assert np.mean(shuf) > 0.8
    assert np.nanmean(purged) < 0.3


def test_deflated_sharpe_penalises_many_trials():
    rng = np.random.default_rng(2)
    trials = rng.standard_normal((2000, 200)) * 0.01  # pure noise strategies
    srs = np.array([sharpe(trials[:, j]) for j in range(200)])
    best = trials[:, int(np.argmax(srs))]
    assert deflated_sharpe(best, srs[:1])["dsr"] > 0.9   # looks great if you hide the trials
    assert deflated_sharpe(best, srs)["dsr"] < 0.9       # deflated against all 200: not significant
    real = rng.standard_normal(2000) * 0.01 + 0.002     # genuine edge, SR 0.2 per period
    assert deflated_sharpe(real, np.append(srs, sharpe(real)))["dsr"] > 0.95


def test_pbo_high_for_noise_low_for_real_edge():
    rng = np.random.default_rng(3)
    noise = rng.standard_normal((1000, 20))
    assert pbo(noise, 8)["pbo"] > 0.3
    edge = noise.copy()
    edge[:, 0] += 0.3
    assert pbo(edge, 8)["pbo"] < 0.05


def test_alpha_pipeline_runs_end_to_end(tmp_path):
    from tickforge import alpha

    fdir = tmp_path / "f"
    fdir.mkdir()
    for i, name in enumerate(["01022019", "01032019", "01042019"]):
        g = random_grid(T=1500, S=3, seed=i)
        hdr = json.dumps({"symbols": g.symbols, "fields": FIELDS, "start_ns": 0, "step_ns": 10**9})
        (fdir / f"{name}.bin").write_bytes(hdr.encode() + b"\n" + g.data.astype("<i8").tobytes())
    old = alpha.SKIP
    alpha.SKIP = 120
    try:
        s = alpha.run(fdir, tmp_path / "out")
    finally:
        alpha.SKIP = old
    assert s["test_days"] == ["20190104"]
    assert s["n_trials"] == 2 * (len(FEATURES) + 2)
    assert (tmp_path / "out" / "graveyard.md").exists()
    # Random data: nothing should survive.
    assert not any(v["dsr"] >= 0.95 and v["net_bps_per_trade"] > 0 for v in s["trials"].values())
