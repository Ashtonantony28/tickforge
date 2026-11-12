# 0002: C++20 engine with pybind11 bindings

- **Status:** Accepted
- **Date:** 2026-11-12

## Context
The engine must replay tens of millions of messages per second and serve as a golden model for RTL,
while research happens in Python.

## Decision
Engine in C++20 (header-mostly, no exceptions on the hot path), exposed to Python via pybind11. Build
with CMake + Ninja. Tests use a small in-tree harness (`engine/tests/tf_test.hpp`) so the build has no
network-fetched dependencies; heavier randomized comparison tests run from pytest through the bindings.

## Consequences
No GoogleTest/Google Benchmark dependency for now; the benchmark is a small timed replay tool. Can be
revisited if the harness becomes limiting.

## Alternatives considered
Rust (strong safety story, less common in HFT C++ shops); pure Python/numba (too slow as golden model).
