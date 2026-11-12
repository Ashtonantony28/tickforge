"""M6: paper engine is record-driven (replay reproduces the log), hash chain detects edits,
fills follow the L2 queue model."""
from __future__ import annotations

import gzip
import json
import random

from tickforge import paper
from tickforge.l2 import fmt_dec

from test_l2 import FakeVenue  # noqa: E402  (tests dir is on sys.path under pytest)


def _feed(seed=0, n=3000):
    v = FakeVenue(seed)
    rng = random.Random(seed)
    out = [v.rec("meta", {"event": "connect"}), v.rec("depth", v.diff()), v.rec("snap", v.snapshot())]
    for i in range(n):
        out.append(v.rec("depth", v.diff()))
        if rng.random() < 0.5:  # trade at a touch
            buyer_maker = rng.random() < 0.5
            px = v.book.best(True)[0] if buyer_maker else v.book.best(False)[0]
            out.append(v.rec("trade", {"e": "trade", "p": fmt_dec(px), "q": fmt_dec(rng.randint(1, 300) * 10**6),
                                       "m": buyer_maker}))
        if i % 500 == 499:
            out.append(v.rec("snap", v.snapshot()))
    return out


def _params():
    return paper.PaperParams(gamma=0.001, k=50.0, size=10**8, max_pos=3 * 10**8, tick=10**6,
                             requote_ms=100, latency_ms=50)


def test_replay_reproduces_live_log(tmp_path):
    recs = _feed(1)
    live = paper.PaperEngine(_params(), tmp_path / "paper.log")
    feed = tmp_path / "feed"
    feed.mkdir()
    with gzip.open(feed / "X-1.jsonl.gz", "wt") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
            live.process(r)
    live.log_f.close()
    assert live.fills > 0
    assert abs(live.pos) <= 3 * 10**8
    v = paper.verify(tmp_path, _params())
    assert v["hash_chain_ok"] and v["replay_prefix_identical"] and v["replay_extra_lines"] == 0


def test_hash_chain_detects_tampering(tmp_path):
    recs = _feed(2, n=800)
    eng = paper.PaperEngine(_params(), tmp_path / "paper.log")
    (tmp_path / "feed").mkdir()
    for r in recs:
        eng.process(r)
    eng.log_f.close()
    lines = (tmp_path / "paper.log").read_text().splitlines()
    e = json.loads(lines[3])
    e["b"] = e["b"].replace('"t":', '"t":1')
    lines[3] = json.dumps(e, separators=(",", ":"))
    (tmp_path / "paper.log").write_text("\n".join(lines) + "\n")
    assert not paper.verify(tmp_path, _params())["hash_chain_ok"]


def test_queue_model_fills_only_after_ahead_consumed():
    eng = paper.PaperEngine(_params())
    eng.orders["B"] = paper.Order("B", 100 * 10**8, 10**8, 3 * 10**8, 0)
    eng.on_trade(1, {"p": "100", "q": "2", "m": True})   # 2 of the 3 ahead
    assert eng.fills == 0 and eng.orders["B"].ahead == 10**8
    eng.on_trade(2, {"p": "100", "q": "1.5", "m": True})  # 1 ahead, then 0.5 fills us
    assert eng.fills == 1 and eng.pos == 5 * 10**7
    eng.on_trade(3, {"p": "99.99", "q": "0.01", "m": True})  # trade through: rest fills
    assert eng.orders["B"] is None and eng.pos == 10**8
    eng.orders["S"] = paper.Order("S", 101 * 10**8, 10**8, 0, 10)
    eng.on_trade(5, {"p": "101", "q": "1", "m": False})  # not live yet (latency)
    assert eng.orders["S"] is not None
