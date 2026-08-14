#!/bin/bash
# Regression test for worker.cpp's checkpoint/resume semantics (BUGS.md #1:
# a checkpoint that records the wrong "next index to process" causes the
# index being processed at checkpoint time to be silently reprocessed and
# duplicated into `out` on resume).
#
# Runs against the actual compiled, BOINC-linked worker_app binary, so unlike
# tests/test_permutation.cpp and tests/test_engine.cpp this must run inside
# the server container (or anywhere worker_app is built) -- it is not part of
# the host-only CI job. Run it after any change to worker.cpp's checkpoint
# logic:
#
#   docker exec --user boincadm boinc_server bash /home/boincadm/projects/camicia/worker/test_resume.sh
#
# (adjust the path if run from elsewhere the binary/script were copied to)
set -eu

WORKER="${1:-$(dirname "$0")/worker_app}"
if [ ! -x "$WORKER" ]; then
    echo "❌ worker_app not found/executable at $WORKER" >&2
    exit 1
fi

WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT
cd "$WORKDIR"

# The Casella 2024 game (see tests/test_data_gen.hpp) is a confirmed "loop"
# result at this exact index -- anchoring the test range here means a
# reintroduced off-by-one reprocesses a *known* loop index, not an arbitrary
# one that might not even be interesting.
MID="472460898658889399111"
START=$(python3 -c "print($MID - 2)")
END=$(python3 -c "print($MID + 2)")
NEXT=$(python3 -c "print($MID + 1)")

count_loop_lines_for_mid() {
    grep -c "^loop,$MID," "$1" || true
}

echo "=== Straight-through run: [$START, $END] ==="
echo "$START $END" > in
"$WORKER"
straight_loop_count=$(count_loop_lines_for_mid out)
echo "loop entries for MID in straight-through run: $straight_loop_count"
rm -f in out camicia_state

echo
echo "=== Resume from a CORRECT checkpoint (cur = MID+1, i.e. 'next index to process') ==="
cat > camicia_state <<EOF
$NEXT $START $END 0 0 0 1
$MID 474 66
EOF
"$WORKER"
correct_loop_count=$(count_loop_lines_for_mid out)
echo "loop entries for MID after correct-checkpoint resume: $correct_loop_count"
rm -f out camicia_state

echo
echo "=== Resume from the OLD BUGGY checkpoint semantics (cur = MID, i.e. 'last index processed') ==="
cat > camicia_state <<EOF
$MID $START $END 0 0 0 1
$MID 474 66
EOF
"$WORKER"
buggy_loop_count=$(count_loop_lines_for_mid out)
echo "loop entries for MID after buggy-checkpoint resume: $buggy_loop_count"
rm -f in out camicia_state

echo
pass=1
if [ "$straight_loop_count" != "1" ]; then
    echo "❌ FAIL: straight-through run should find exactly 1 loop entry for MID, got $straight_loop_count"
    pass=0
fi
if [ "$correct_loop_count" != "1" ]; then
    echo "❌ FAIL: correct-checkpoint resume should find exactly 1 loop entry for MID, got $correct_loop_count"
    pass=0
fi
if [ "$buggy_loop_count" != "2" ]; then
    echo "❌ FAIL: buggy-checkpoint resume was expected to demonstrate the duplicate (2 entries), got $buggy_loop_count -- either the bug is back, or this test no longer has power to catch it"
    pass=0
fi

if [ "$pass" = "1" ]; then
    echo "✅ PASS: current checkpoint semantics produce no duplicate, and the test correctly demonstrates the old bug's symptom under buggy semantics"
    exit 0
else
    exit 1
fi
