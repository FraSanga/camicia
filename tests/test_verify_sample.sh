#!/usr/bin/env bash
# Integration test for tools/verify_sample: builds it and a small test-only
# reference generator (gen_reference_output.cpp, which replays worker.cpp's
# own simulate-and-record loop), then checks that verify_sample (a) accepts
# correct ground truth with zero anomalies and (b) flags each of the two
# ways a WU's recorded output can be inconsistent with the real engine.
set -euo pipefail
cd "$(dirname "$0")/.."

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

g++ -std=c++17 -O2 tools/verify_sample/verify_sample.cpp \
    tools/worker/core/permutation.cpp tools/worker/core/engine.cpp \
    -o "$TMP/verify_sample" -I tools/worker/core

g++ -std=c++17 -O2 tests/gen_reference_output.cpp \
    tools/worker/core/permutation.cpp tools/worker/core/engine.cpp \
    -o "$TMP/gen_ref" -I tools/worker/core

# Deliberately small range straddling the known "Casella 2024" loop index
# (tests/test_data_gen.hpp) -- gives real loop AND finished lines to check
# both consistency rules without brute-forcing a search for a rare loop.
START=472460898658889399106
END=472460898658889399116
"$TMP/gen_ref" "$START" "$END" "$TMP/ref_out.txt"

fail() { echo "FAIL: $1"; exit 1; }

# --- estimate-only arithmetic: N=100, defect-rate=0.1, confidence=0.95 ---
# hand-computed: ceil(ln(0.05)/ln(0.9)) = ceil(2.9957.../0.10536...) = 29
EST=$("$TMP/verify_sample" --start 0 --end 99 --estimate-only --confidence 0.95 --defect-rate 0.1 | grep -oE 'n = [0-9]+' | grep -oE '[0-9]+')
[ "$EST" = "29" ] || fail "estimate-only: expected n=29, got n=$EST"
echo "OK: estimate-only sample-size arithmetic"

# --- correct ground truth: full sample (--samples matches block size), expect PASS ---
"$TMP/verify_sample" --start "$START" --end "$END" --result-file "$TMP/ref_out.txt" --samples 11 > "$TMP/pass_out.txt"
echo "OK: correct ground truth passes (exit 0)"

# --- corrupted: bump the recorded best's cards so it no longer matches the true value ---
sed 's/finished,472460898658889399110,163,21/finished,472460898658889399110,165,21/' "$TMP/ref_out.txt" > "$TMP/bad_best.txt"
if "$TMP/verify_sample" --start "$START" --end "$END" --result-file "$TMP/bad_best.txt" --samples 11 > "$TMP/bad_best_report.txt"; then
    fail "corrupted best-finished value was not detected"
fi
grep -q "recorded best-finished value mismatch" "$TMP/bad_best_report.txt" || fail "wrong anomaly reported for corrupted best-finished value"
echo "OK: corrupted best-finished value is detected"

# --- corrupted: drop the recorded loop line entirely ---
grep -v '^loop,' "$TMP/ref_out.txt" > "$TMP/bad_loop.txt"
if "$TMP/verify_sample" --start "$START" --end "$END" --result-file "$TMP/bad_loop.txt" --samples 11 > "$TMP/bad_loop_report.txt"; then
    fail "missing loop line was not detected"
fi
grep -q "true outcome is a loop" "$TMP/bad_loop_report.txt" || fail "wrong anomaly reported for missing loop line"
echo "OK: missing loop line is detected"

echo "All test_verify_sample.sh checks passed."
