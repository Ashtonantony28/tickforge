"""Microstructure features and labels from tf_features grids (M4, ADR 0009).

`tf_features` writes cumulative raw counters sampled every second; everything windowed is derived
here, so it is easy to audit that a feature at time t only uses rows <= t (see
tests/test_alpha.py::test_features_are_causal).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

TICK = 100  # ITCH price units per $0.01


@dataclass
class Grid:
    symbols: list[str]
    fields: list[str]
    start_ns: int
    step_ns: int
    data: np.ndarray  # [T, S, F] int64

    def col(self, name: str) -> np.ndarray:
        return self.data[:, :, self.fields.index(name)].astype(np.float64)


def load_grid(path: Path) -> Grid:
    raw = Path(path).read_bytes()
    nl = raw.index(b"\n")
    hdr = json.loads(raw[:nl])
    S, F = len(hdr["symbols"]), len(hdr["fields"])
    arr = np.frombuffer(raw[nl + 1:], dtype="<i8")
    arr = arr[: arr.size - arr.size % (S * F)].reshape(-1, S, F)
    return Grid(hdr["symbols"], hdr["fields"], hdr["start_ns"], hdr["step_ns"], arr)


def _lag_diff(x: np.ndarray, w: int) -> np.ndarray:
    """x[t] - x[t-w] along axis 0, NaN for t < w (causal)."""
    out = np.full_like(x, np.nan)
    out[w:] = x[w:] - x[:-w]
    return out


def _safe_div(a, b):
    with np.errstate(divide="ignore", invalid="ignore"):
        r = a / b
    r[~np.isfinite(r)] = np.nan
    return r


FEATURES = [
    "spread_ticks", "imb1", "imb5", "micro_dev", "count_imb1",
    "ofi_1", "ofi_5", "ofi_30", "tfi_5", "tfi_30",
    "ret_5", "ret_30", "ret_300", "cxl_imb_5", "add_imb_5", "log_trades_30", "vol_300",
]


def make_features(g: Grid) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (X [T,S,K], mid [T,S] in $, valid [T,S]). All features use rows <= t only."""
    bp, bq, ap, aq = g.col("bid_px"), g.col("bid_qty"), g.col("ask_px"), g.col("ask_qty")
    valid = (bp > 0) & (ap > bp) & (bq > 0) & (aq > 0)
    mid = np.where(valid, (bp + ap) / 2, np.nan)
    spread = (ap - bp) / TICK
    micro = _safe_div(bp * aq + ap * bq, bq + aq)
    depth1 = (bq + aq) / 2
    bd5, ad5 = g.col("bid_depth5"), g.col("ask_depth5")
    ofi = g.col("ofi")
    bv, sv = g.col("buy_vol"), g.col("sell_vol")
    cb, ca = g.col("cxl_bid"), g.col("cxl_ask")
    ab, aa = g.col("add_bid"), g.col("add_ask")
    logmid = np.log(mid)
    r1 = _lag_diff(logmid, 1)

    feats = {
        "spread_ticks": spread,
        "imb1": _safe_div(bq - aq, bq + aq),
        "imb5": _safe_div(bd5 - ad5, bd5 + ad5),
        "micro_dev": (micro - (bp + ap) / 2) / TICK,
        "count_imb1": _safe_div(g.col("bid_count1") - g.col("ask_count1"),
                                g.col("bid_count1") + g.col("ask_count1")),
        "ofi_1": _safe_div(_lag_diff(ofi, 1), depth1),
        "ofi_5": _safe_div(_lag_diff(ofi, 5), depth1),
        "ofi_30": _safe_div(_lag_diff(ofi, 30), depth1),
        "tfi_5": _safe_div(_lag_diff(bv - sv, 5), _lag_diff(bv + sv, 5) + 1),
        "tfi_30": _safe_div(_lag_diff(bv - sv, 30), _lag_diff(bv + sv, 30) + 1),
        "ret_5": _lag_diff(logmid, 5) * 1e4,
        "ret_30": _lag_diff(logmid, 30) * 1e4,
        "ret_300": _lag_diff(logmid, 300) * 1e4,
        "cxl_imb_5": _safe_div(_lag_diff(ca - cb, 5), _lag_diff(ca + cb, 5) + 1),
        "add_imb_5": _safe_div(_lag_diff(ab - aa, 5), _lag_diff(ab + aa, 5) + 1),
        "log_trades_30": np.log1p(np.maximum(_lag_diff(g.col("trades"), 30), 0)),
        "vol_300": _rolling_std(r1, 300) * 1e4,
    }
    X = np.stack([feats[k] for k in FEATURES], axis=-1)
    return X, mid / 1e4, valid


def _rolling_std(x: np.ndarray, w: int) -> np.ndarray:
    x0 = np.nan_to_num(x)
    c1 = np.cumsum(x0, axis=0)
    c2 = np.cumsum(x0 * x0, axis=0)
    s1 = _lag_diff(c1, w)
    s2 = _lag_diff(c2, w)
    var = np.maximum(s2 / w - (s1 / w) ** 2, 0)
    return np.sqrt(var)


def forward_return_bps(mid: np.ndarray, h: int) -> np.ndarray:
    """Label: log mid return from t to t+h in bps (NaN where t+h is past the end)."""
    out = np.full_like(mid, np.nan)
    out[:-h] = (np.log(mid[h:]) - np.log(mid[:-h])) * 1e4
    return out
