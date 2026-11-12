"""M5 market-making study on real ITCH days (ADR 0010).

    python -m tickforge.mm_study data/cache/itch_sub --out docs/results

Protocol: the (gamma, k) grid and the alpha skew are chosen on the first TUNE_DAYS days only
(total net P&L across symbols); the chosen configs, plain A-S (alpha 0) and the alpha-skewed
variant, are then run once on every later day. Every grid point is a trial for the deflated
Sharpe, which uses per-symbol-day net P&L as the return series.
"""
from __future__ import annotations

import argparse
import itertools
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from .alpha import day_key
from .research import deflated_sharpe, sharpe

SYMBOLS = ["AAPL", "MSFT", "AMZN", "GOOGL", "FB", "INTC", "CSCO", "CMCSA", "PEP", "NVDA", "NFLX", "QQQ",
           "AMD", "MU", "TSLA"]
GAMMAS = (0.01, 0.1, 1.0)
KS = (100.0, 200.0, 400.0)
ALPHAS = (0.0, 1.0, 2.0)
TUNE_DAYS = 2
LATENCY_NS = 20_000
ROOT = Path(__file__).resolve().parents[2]


def run_one(itch: Path, sym: str, gamma: float, k: float, alpha: float, build: Path) -> dict:
    cmd = [str(build / "engine" / "tf_mm"), str(itch), "--symbol", sym, "--gamma", str(gamma), "--k", str(k),
           "--alpha-ticks", str(alpha), "--latency-ns", str(LATENCY_NS)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    d.pop("equity_by_minute")
    return d


def sweep(paths, configs, build, workers=4):
    jobs = [(p, s, c) for p in paths for c in configs for s in SYMBOLS]
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(lambda j: run_one(j[0], j[1], *j[2], build), jobs))
    out = {}
    for (p, s, c), r in zip(jobs, res):
        out.setdefault(c, {}).setdefault(day_key(p), {})[s] = r
    return out


def totals(by_day: dict) -> dict:
    keys = ["net_usd", "gross_usd", "spread_capture_usd", "adverse_selection_usd", "inventory_other_usd",
            "fee_rebate_usd", "maker_shares"]
    out = {k: round(sum(r[k] for d in by_day.values() for r in d.values()), 2) for k in keys}
    # Symbol-days that ended with inventory (should be 0: the strategy flattens by 15:55).
    out["not_flat_symbol_days"] = sum(r["final_position"] != 0 for d in by_day.values() for r in d.values())
    return out


def series(by_day: dict) -> np.ndarray:
    return np.array([r["net_usd"] for d in sorted(by_day) for r in by_day[d].values()])


def run(sub_dir: Path, out_dir: Path, build: Path) -> dict:
    paths = sorted(sub_dir.glob("*.itch"), key=day_key)
    tune, test = paths[:TUNE_DAYS], paths[TUNE_DAYS:]
    grid = list(itertools.product(GAMMAS, KS, ALPHAS))
    tune_res = sweep(tune, grid, build)
    tune_tot = {c: totals(v) for c, v in tune_res.items()}
    best_base = max((c for c in grid if c[2] == 0.0), key=lambda c: tune_tot[c]["net_usd"])
    best_alpha = max((c for c in grid if c[2] > 0.0), key=lambda c: tune_tot[c]["net_usd"])
    chosen = [best_base, best_alpha]
    test_res = sweep(test, chosen, build) if test else {}
    sr_trials = np.array([sharpe(series(tune_res[c])) for c in grid])
    report = {"tune_days": [day_key(p) for p in tune], "test_days": [day_key(p) for p in test],
              "symbols": SYMBOLS, "latency_ns": LATENCY_NS, "grid_size": len(grid),
              "tune": {f"g={c[0]} k={c[1]} a={c[2]}": tune_tot[c] for c in grid},
              "chosen": {"as": best_base, "as_alpha": best_alpha}, "test": {}}
    for name, c in (("as", best_base), ("as_alpha", best_alpha)):
        if c not in test_res:
            continue
        r = series(test_res[c])
        ds = deflated_sharpe(r, sr_trials)  # the 27 grid trials; the chosen config is one of them
        per_day = {d: round(sum(x["net_usd"] for x in v.values()), 2) for d, v in sorted(test_res[c].items())}
        report["test"][name] = {"config": c, "totals": totals(test_res[c]), "net_by_day": per_day,
                                "per_symbol_day_sharpe": round(ds["sr"], 3), "dsr": round(ds["dsr"], 4),
                                "positive_symbol_days": int((r > 0).sum()), "symbol_days": int(len(r)),
                                "by_symbol": {s: round(sum(test_res[c][d][s]["net_usd"] for d in test_res[c]), 2)
                                              for s in SYMBOLS}}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "m5-mm.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("sub_dir", type=Path)
    p.add_argument("--out", type=Path, default=Path("docs/results"))
    p.add_argument("--build", type=Path, default=ROOT / "build")
    a = p.parse_args(argv)
    r = run(a.sub_dir, a.out, a.build)
    print(json.dumps({k: v for k, v in r.items() if k != "tune"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
