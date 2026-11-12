"""Tickforge Python package: ITCH encoding, reference order book, synthetic market generator.

The compiled engine module (`tickforge._tickforge`) is optional; `load_engine()` finds it in the
package directory or in a CMake build directory named by $TF_BUILD_DIR (default: build/).
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

__all__ = ["load_engine"]


def load_engine():
    try:
        return importlib.import_module("tickforge._tickforge")
    except ImportError:
        pass
    root = Path(__file__).resolve().parents[2]
    build = Path(os.environ.get("TF_BUILD_DIR", root / "build"))
    if not build.is_absolute():
        build = root / build
    for cand in [build / "bindings", build]:
        if any(cand.glob("_tickforge*.so")) or any(cand.glob("_tickforge*.pyd")):
            sys.path.insert(0, str(cand))
            return importlib.import_module("_tickforge")
    raise ImportError(f"_tickforge extension not found (looked in {build}); build with CMake first")
