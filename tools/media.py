"""Render the README demo media into docs/media/.

    python3 tools/media.py

  book-replay.{gif,mp4,png}  the Python reference book (refbook.py, bit-exact with the C++ engine) replaying the
                             committed SYNTHETIC ITCH fixture fixtures/synth_small.itch, one stock's ladder,
                             touch and executions; the run asserts the final digest equals the fixture's
  mm-pnl.png                 the market-making P&L decomposition on 5 held-out real Nasdaq days, parsed from the
                             aggregate table in docs/results/m5-mm.md (no market data is read or committed)

Needs matplotlib and ffmpeg. Deterministic: no clocks, no RNG.
"""
from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402,F401

from tickforge.itch import decode  # noqa: E402
from tickforge.refbook import RefReplayer  # noqa: E402

BG, PANEL, GRID, TXT, DIM = "#0d1117", "#161b22", "#30363d", "#e6edf3", "#8b949e"
BID, ASK, YEL, BLUE = "#3fb950", "#f85149", "#e3b341", "#58a6ff"
MONO = "DejaVu Sans Mono"
OUT = ROOT / "docs" / "media"
FIX = ROOT / "fixtures" / "synth_small.itch"
PX = 1e4                                   # ITCH prices are in 1/10,000 dollar
LEVELS = 8
NAMES = {"A": "ADD", "E": "EXECUTE", "X": "CANCEL", "D": "DELETE", "U": "REPLACE"}


def frames(buf):
    off = 0
    while off + 2 <= len(buf):
        (ln,) = struct.unpack_from(">H", buf, off)
        yield buf[off: off + 2 + ln]
        off += 2 + ln


def replay(locate):
    """Feed the fixture one framed message at a time; record `locate`'s book after each of its messages."""
    rep = RefReplayer()
    b = rep.books
    snaps = []
    for fr in frames(FIX.read_bytes()):
        (m,) = list(decode(fr))
        info = None
        if m.locate == locate and m.type in NAMES:
            ref = m.fields[0]
            o = b.orders.get(ref)
            if m.type == "A":
                info = ("A", m.fields[1], m.fields[2], m.fields[3])
            elif o is not None:
                side, price = o[1], o[2]
                if m.type == "U":
                    info = ("U", side, m.fields[2], m.fields[3])
                elif m.type == "D":
                    info = ("D", side, o[3], price)
                else:
                    info = (m.type, side, min(m.fields[1], o[3]), price)
        rep.feed(fr)
        if info is not None:
            snaps.append(dict(msg=rep.messages, info=info, bid=b.depth(locate, "B", LEVELS),
                              ask=b.depth(locate, "S", LEVELS)))
    exp = json.loads((ROOT / "fixtures" / "synth_small.expected.json").read_text())
    assert f"{rep.digest:016x}" == exp["digest"] and rep.messages == exp["messages"], "replay != fixture"
    return snaps, rep, exp


def draw(fig, snaps, i, sym, total_msgs, digest_ok):
    s = snaps[i]
    fig.clf()
    fig.text(0.035, 0.935, "Tickforge: rebuilding a limit order book from ITCH 5.0", fontsize=18, weight="bold", color=TXT)
    fig.text(0.035, 0.895, "SYNTHETIC data: the committed, seeded fixture fixtures/synth_small.itch (no real market data)  ·  "
             "Python reference book, bit-exact with the C++ engine (digest checked)", fontsize=9.3, color=DIM)
    # ladder
    ax = fig.add_axes([0.085, 0.12, 0.38, 0.70])
    ax.set_facecolor(PANEL)
    rows = [(p, q, c, "S", LEVELS + k) for k, (p, q, c) in enumerate(s["ask"])] + \
           [(p, q, c, "B", LEVELS - 1 - k) for k, (p, q, c) in enumerate(s["bid"])]
    qmax = max(QMAX, 1)
    info = s["info"]
    for p, q, c, side, y in rows:
        col = ASK if side == "S" else BID
        hit = p == info[3] and side == info[1]
        ax.barh(y, q / qmax, color=col, alpha=0.95 if hit else 0.55, height=0.78)
        ax.text(-0.02, y, f"{p / PX:,.2f}", ha="right", va="center", color=col, fontsize=10.5, family=MONO,
                weight="bold" if hit else "normal")
        ax.text(q / qmax + 0.015, y, f"{q:,}  ({c} order{'s' if c != 1 else ''})", va="center",
                color=TXT if hit else DIM, fontsize=9, family=MONO)
    if s["bid"] and s["ask"]:
        spr = (s["ask"][0][0] - s["bid"][0][0]) / PX
        ax.axhline(LEVELS - 0.5, color=YEL, lw=0.8, ls=":")
        ax.text(1.0, LEVELS - 0.5, f"spread {spr:.2f}", color=YEL, fontsize=8.5, ha="right", va="center",
                bbox=dict(fc=PANEL, ec="none", pad=1))
    ax.set_xlim(0, 1.28); ax.set_ylim(-0.7, 2 * LEVELS - 0.3)
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_color(GRID)
    ax.set_title(f"{sym}: top {LEVELS} price levels (asks red, bids green)", color=TXT, fontsize=11, loc="left")
    t, side, sh, p = info
    word = NAMES[t]
    fig.text(0.035, 0.065, f"message {s['msg']:>6,} / {total_msgs:,}   {word:<8} {'BUY ' if side == 'B' else 'SELL'} "
             f"{sh:>5,} @ {p / PX:,.2f}", color=YEL if t == "E" else TXT, fontsize=12, family=MONO)
    # touch history
    ax = fig.add_axes([0.53, 0.42, 0.44, 0.40])
    ax.set_facecolor(BG)
    lo = max(0, i - WINDOW)
    xs = list(range(lo, i + 1))
    bb = [snaps[j]["bid"][0][0] / PX if snaps[j]["bid"] else float("nan") for j in xs]
    aa = [snaps[j]["ask"][0][0] / PX if snaps[j]["ask"] else float("nan") for j in xs]
    ax.step(xs, aa, color=ASK, lw=1.4, where="post", label="best ask")
    ax.step(xs, bb, color=BID, lw=1.4, where="post", label="best bid")
    ex = [(j, snaps[j]["info"][3] / PX) for j in xs if snaps[j]["info"][0] == "E"]
    if ex:
        ax.scatter(*zip(*ex), s=14, color=YEL, zorder=3, label="execution")
    ax.set_xlim(lo, lo + WINDOW)
    ax.tick_params(colors=DIM, labelsize=8)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(GRID)
    ax.set_xlabel(f"{sym} messages", color=DIM, fontsize=9)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.2f}"))
    ax.margins(y=0.25)
    y0, y1 = ax.get_ylim()
    ax.yaxis.set_major_locator(matplotlib.ticker.MultipleLocator(0.01) if y1 - y0 < 0.1
                               else matplotlib.ticker.MaxNLocator(6, steps=[1, 2, 5, 10]))
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), fontsize=8, frameon=False, labelcolor=TXT, ncol=3)
    ax.set_title("touch and executions", color=TXT, fontsize=11, loc="left", pad=18)
    # message mix
    ax = fig.add_axes([0.53, 0.12, 0.44, 0.20])
    ax.set_facecolor(BG)
    cnt = {k: 0 for k in NAMES}
    for j in range(i + 1):
        cnt[snaps[j]["info"][0]] += 1
    keys = list(NAMES)
    ax.barh(range(len(keys))[::-1], [cnt[k] for k in keys], color=[BLUE, YEL, DIM, DIM, BLUE], height=0.65)
    for k, kk in enumerate(keys):
        ax.text(cnt[kk] + MIXMAX * 0.01, len(keys) - 1 - k, f"{cnt[kk]:,}", va="center", color=TXT, fontsize=8.5)
    ax.set_yticks(range(len(keys))[::-1]); ax.set_yticklabels([NAMES[k].lower() for k in keys], color=TXT, fontsize=8.5)
    ax.set_xlim(0, MIXMAX * 1.12); ax.set_xticks([])
    for sp in ("top", "right", "bottom"):
        ax.spines[sp].set_visible(False)
    ax.spines["left"].set_color(GRID)
    ax.set_title(f"{sym} message mix so far", color=DIM, fontsize=9, loc="left")
    if digest_ok:
        fig.text(0.53, 0.05, "replay complete: digest after every update matches the fixture ✓", color=BID, fontsize=11,
                 weight="bold")


QMAX = MIXMAX = WINDOW = 1


def book_replay(locate=1, fps=15, n_frames=240):
    global QMAX, MIXMAX, WINDOW
    snaps, rep, exp = replay(locate)
    sym = rep.symbols.get(locate, f"locate {locate}")
    QMAX = max(q for s in snaps for side in ("bid", "ask") for _, q, _ in s[side])
    MIXMAX = max(sum(1 for s in snaps if s["info"][0] == k) for k in NAMES)
    WINDOW = len(snaps) // 3
    step = max(1, len(snaps) // n_frames)
    idx = list(range(0, len(snaps), step))
    if idx[-1] != len(snaps) - 1:
        idx.append(len(snaps) - 1)
    tmp = Path(tempfile.mkdtemp())
    fig = plt.figure(figsize=(12.8, 7.2), dpi=100, facecolor=BG)
    n = 0
    for i in idx:
        draw(fig, snaps, i, sym, exp["messages"], False)
        fig.savefig(tmp / f"f{n:04d}.png", facecolor=BG); n += 1
    for _ in range(fps * 3):
        draw(fig, snaps, idx[-1], sym, exp["messages"], True)
        fig.savefig(tmp / f"f{n:04d}.png", facecolor=BG); n += 1
    fig.savefig(OUT / "book-replay.png", facecolor=BG)
    plt.close(fig)
    ff = ["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(fps), "-i", str(tmp / "f%04d.png")]
    subprocess.run(ff + ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", str(OUT / "book-replay.mp4")], check=True)
    subprocess.run(ff + ["-vf", "fps=10,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=48[p];"
                         "[b][p]paletteuse=dither=none", str(OUT / "book-replay.gif")], check=True)
    shutil.rmtree(tmp)


def mm_pnl():
    text = (ROOT / "docs" / "results" / "m5-mm.md").read_text()
    comp = []
    for ln in text.splitlines():
        cells = [c.strip().strip("*") for c in ln.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0] in ("Spread capture", "Adverse selection (10 s)", "Inventory / flattening",
                                            "Maker rebates", "Net"):
            comp.append((cells[0], int(cells[1].replace(",", "").replace("+", ""))))
        if len(comp) == 5:
            break
    fig, ax = plt.subplots(figsize=(12.8, 5.0), dpi=100, facecolor=BG)
    ax.set_facecolor(BG)
    run = 0
    for k, (name, v) in enumerate(comp):
        if name == "Net":
            ax.bar(k, v, color=ASK if v < 0 else BID, width=0.6)
            y = v
        else:
            ax.bar(k, v, bottom=run, color=BID if v >= 0 else ASK, width=0.6, alpha=0.85)
            run += v
            y = run
        ax.text(k, y + (6000 if v >= 0 else -6000), f"{v:+,}", ha="center", va="bottom" if v >= 0 else "top",
                color=TXT, fontsize=11, family=MONO)
        if k < len(comp) - 1 and name != "Net":
            ax.plot([k + 0.3, k + 0.7], [run, run], color=DIM, lw=0.8, ls=":")
    ax.axhline(0, color=GRID)
    ax.set_xticks(range(len(comp))); ax.set_xticklabels([c[0] for c in comp], color=TXT, fontsize=10.5)
    ax.tick_params(colors=DIM)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"${v / 1000:,.0f}k"))
    lo = min(0, min(c[1] for c in comp)) * 1.2
    ax.set_ylim(min(lo, -170000), 150000)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(GRID)
    fig.suptitle("Avellaneda-Stoikov market making: spread earned, then lost to adverse selection", color=TXT, fontsize=14,
                 weight="bold", x=0.02, ha="left")
    fig.text(0.02, 0.885, "Backtest on 5 held-out real Nasdaq days, 15 names, exact queue-position fills, latency and fees. "
             "Aggregate P&L from docs/results/m5-mm.md.", color=DIM, fontsize=9.5)
    fig.tight_layout(rect=(0, 0, 1, 0.87))
    fig.savefig(OUT / "mm-pnl.png", facecolor=BG)
    plt.close(fig)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    mm_pnl()
    book_replay()


if __name__ == "__main__":
    main()
