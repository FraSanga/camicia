#!/bin/bash
# Regression test for worker.cpp's checkpoint/resume semantics.
#
# Checkpointing is split across two files: camicia_state (a small header,
# rewritten atomically via temp+rename on every checkpoint) and
# camicia_loops (append-only, one line per loop found -- only the delta
# since the last checkpoint gets appended, instead of the whole list being
# re-serialized every time). See worker.cpp's own comments on do_checkpoint()
# for the full crash-safety reasoning; this test exercises it end to end
# against the real compiled binary.
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
rm -f in out camicia_state camicia_loops

echo
echo "=== Resume from a CORRECT checkpoint (cur = MID+1, i.e. 'next index to process') ==="
cat > camicia_state <<EOF
$NEXT $START $END 0 0 0
EOF
cat > camicia_loops <<EOF
$MID 474 66
EOF
"$WORKER"
correct_loop_count=$(count_loop_lines_for_mid out)
echo "loop entries for MID after correct-checkpoint resume: $correct_loop_count"
rm -f out camicia_state camicia_loops

echo
echo "=== Resume simulating a crash between the loops-file append and the header update ==="
echo "    (header still at cur = MID, i.e. 'last index processed' -- either the pre-fix"
echo "     off-by-one bug, or a real crash landing between do_checkpoint()'s two writes --"
echo "     but camicia_loops already durably has MID's result from before the crash.)"
cat > camicia_state <<EOF
$MID $START $END 0 0 0
EOF
cat > camicia_loops <<EOF
$MID 474 66
EOF
"$WORKER"
crash_window_loop_count=$(count_loop_lines_for_mid out)
echo "loop entries for MID after crash-window resume: $crash_window_loop_count"
rm -f in out camicia_state camicia_loops

echo
echo "=== Orphaned camicia_loops: camicia_state lost entirely, then a real second resume ==="
echo "    (the actual bug cce5745 fixes -- and the one scenario the three tests above can't"
echo "     reach, since worker's final 'out' is always written fresh from in-memory state,"
echo "     never by re-reading camicia_loops within the same run. Needs a real checkpoint to"
echo "     really append to disk, and a real second resume to read that disk state back in,"
echo "     which is what CAMICIA_FORCE_CHECKPOINT (worker.cpp's should_checkpoint()) is for.)"
echo "    Run 1: [$START, $MID] with forced checkpointing -- ends with camicia_state"
echo "    (cur=$NEXT) and camicia_loops (1 entry for MID) genuinely on disk."
echo "$START $MID" > in
CAMICIA_FORCE_CHECKPOINT=1 "$WORKER"
rm -f camicia_state  # the crash: header lost, loops file survives
echo "    Run 2: reprocess the full [$START, $END] range from scratch (no camicia_state),"
echo "    forced checkpointing again -- re-finds MID; on disk this either duplicates the"
echo "    stale entry (pre-fix) or replaces it after truncating (post-fix)."
echo "$START $END" > in
CAMICIA_FORCE_CHECKPOINT=1 "$WORKER"
echo "    Run 3: normal resume from run 2's real checkpoint (cur past $END, so no new"
echo "    processing happens) -- just reads camicia_loops back into memory and writes it"
echo "    to out, which is where a disk-level duplicate would finally become observable."
"$WORKER"
orphaned_loops_count=$(count_loop_lines_for_mid out)
echo "loop entries for MID after the orphaned-loops-file sequence: $orphaned_loops_count"
rm -f in out camicia_state camicia_loops

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
if [ "$crash_window_loop_count" != "1" ]; then
    echo "❌ FAIL: crash-window resume should still find exactly 1 loop entry for MID (the dedup"
    echo "   guard in main()'s loop should skip re-adding an index already loaded from"
    echo "   camicia_loops) -- got $crash_window_loop_count. If this is 2, the dedup guard"
    echo "   regressed and MID got recorded twice; if this is 0, the guard is over-matching"
    echo "   and silently dropping a real result."
    pass=0
fi
if [ "$orphaned_loops_count" != "1" ]; then
    echo "❌ FAIL: after camicia_state is lost entirely and a real second resume reads back"
    echo "   camicia_loops, there should be exactly 1 entry for MID (cce5745's truncation-on-"
    echo "   non-resume fix) -- got $orphaned_loops_count. If this is 2, that fix regressed and"
    echo "   the loops file is accumulating duplicates across crashed attempts again."
    pass=0
fi

if [ "$pass" = "1" ]; then
    echo "✅ PASS: current checkpoint semantics produce no duplicate under a normal resume,"
    echo "   the dedup guard correctly prevents a duplicate even when camicia_state and"
    echo "   camicia_loops are left inconsistent by a simulated crash between the two writes,"
    echo "   and a genuinely orphaned camicia_loops file (camicia_state lost entirely) gets"
    echo "   truncated rather than duplicated across a real second resume."
    exit 0
else
    exit 1
fi
