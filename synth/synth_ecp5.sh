#!/bin/bash
# Open-source synthesis + place-and-route of the ITCH feed handler for a Lattice ECP5-85F (M8).
# Tools: YoWASP builds of Yosys (with the built-in slang SystemVerilog frontend) and nextpnr-ecp5,
#   pip install yowasp-yosys yowasp-nextpnr-ecp5
# usage: synth/synth_ecp5.sh AW LEVELS [FREQ_MHZ]   (run from the repo root; outputs in build-synth/)
# YoWASP tools run sandboxed and can only see the current directory, hence relative paths.
set -euo pipefail
AW=${1:-8}; LEVELS=${2:-16}; FREQ=${3:-200}
TAG="aw${AW}_l${LEVELS}"
mkdir -p build-synth
SRC="rtl/tf_itch_pkg.sv rtl/tf_itch_parser.sv rtl/tf_order_table.sv rtl/tf_price_levels.sv rtl/tf_itch_book.sv"
CMD="read_slang -j 1 --unroll-limit 1000000 -G AW=${AW} -G LEVELS=${LEVELS} ${SRC} --top tf_itch_book;"
CMD+=" synth_ecp5 -top tf_itch_book -json build-synth/${TAG}.json; tee -o build-synth/stat_${TAG}.txt stat"
yowasp-yosys -q -l "build-synth/yosys_${TAG}.log" -p "${CMD}"
# --out-of-context: the core has ~460 top-level ports (debug counters, record outputs); it is timed as
# a block without IO buffers, as it would sit behind a MAC/DMA wrapper on a real board.
yowasp-nextpnr-ecp5 --85k --package CABGA381 --json "build-synth/${TAG}.json" --freq "${FREQ}" \
  --threads 1 --seed 1 --out-of-context --report "build-synth/pnr_${TAG}.json" --log "build-synth/pnr_${TAG}.log"
