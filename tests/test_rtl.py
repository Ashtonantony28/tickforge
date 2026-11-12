"""Runs the M7 cocotb bench (verif/test_itch_book.py) under Verilator from pytest.

Skipped when Verilator or cocotb is unavailable (`pip install verilator cocotb` provides both).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RTL = ["tf_itch_pkg.sv", "tf_itch_parser.sv", "tf_order_table.sv", "tf_price_levels.sv", "tf_itch_book.sv"]


def _verilator_on_path() -> bool:
    if shutil.which("verilator"):
        return True
    try:
        import verilator  # pip wheel ships the binary inside the package
    except ImportError:
        return False
    bindir = Path(verilator.__file__).parent / "bin"
    if (bindir / "verilator").exists():
        os.environ["PATH"] = f"{bindir}{os.pathsep}{os.environ['PATH']}"
        return True
    return False


pytestmark = pytest.mark.skipif(not _verilator_on_path(), reason="verilator not installed")
cocotb_tools = pytest.importorskip("cocotb_tools.runner")


@pytest.fixture(scope="module")
def runner(tmp_path_factory):
    r = cocotb_tools.get_runner("verilator")
    r.build(sources=[ROOT / "rtl" / f for f in RTL], hdl_toplevel="tf_itch_book",
            build_dir=tmp_path_factory.mktemp("rtl_build"), build_args=["-Wall", "-Wno-fatal"],
            always=True)
    return r


CASES = [
    ("synth_small", 1, 0),
    ("synth_small", 2, 0),
    ("synth_small", 3, 25),   # random input idle cycles
    ("synth_faults", 1, 0),   # malformed traffic: unknown refs, over-fills, duplicates, bad lengths
    ("synth_faults", 2, 10),
    ("random", 1, 5),         # fresh 40k-message stream, 2 instruments
]


@pytest.mark.parametrize("stem,locate,gap", CASES)
def test_rtl_matches_engine(runner, tmp_path, stem, locate, gap):
    result = tmp_path / "summary.json"
    if stem == "random":
        sys.path.insert(0, str(ROOT / "python"))
        from tickforge import synth
        itch_file = tmp_path / "random.itch"
        itch_file.write_bytes(synth.generate(40_000, seed=2026, n_stocks=2, fault_rate=0.01))
    else:
        itch_file = ROOT / "fixtures" / f"{stem}.itch"
    env = {
        "TF_ITCH_FILE": str(itch_file),
        "TF_LOCATE": str(locate),
        "TF_GAP_PCT": str(gap),
        "TF_RESULT": str(result),
        "PYTHONPATH": os.pathsep.join([str(ROOT / "python"), str(ROOT / "verif"), os.environ.get("PYTHONPATH", "")]),
        "TF_BUILD_DIR": str(Path(os.environ.get("TF_BUILD_DIR", ROOT / "build")).resolve()),
    }
    xml = runner.test(hdl_toplevel="tf_itch_book", test_module="test_itch_book", test_dir=ROOT / "verif",
                      extra_env=env, results_xml=str(tmp_path / "results.xml"))
    n_tests, n_failed = cocotb_tools.get_results(xml)
    summary = json.loads(result.read_text()) if result.exists() else {}
    print(json.dumps(summary), file=sys.stderr)
    assert n_tests == 1 and n_failed == 0, summary
    assert summary["digest"] == summary["expected_digest"]
