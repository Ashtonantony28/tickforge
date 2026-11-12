# Report targets (plan sec. 6). Data comes from data/fetch.py / tf_features; nothing here downloads.
PY ?= python3
FEATURES ?= data/cache/features

.PHONY: build test report-alpha
build:
	cmake -S . -B build -G Ninja && cmake --build build

test: build
	ctest --test-dir build --output-on-failure && $(PY) -m pytest -q

# Regenerates docs/results/m4-alpha.json and graveyard.md from the feature grids.
report-alpha:
	PYTHONPATH=python $(PY) -m tickforge.alpha $(FEATURES) --out docs/results
