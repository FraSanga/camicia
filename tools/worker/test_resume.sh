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
echo "=== Mismatched checkpoint: claims the [MID-2, MID+2] range, but this WU's real input"
echo "    file says a completely different, non-overlapping [0, 1] ==="
echo "    (a real incident: a client-side project reset mid-task caused repeated re-dispatch"
echo "     of one huge WU, and a tiny, unrelated WU scheduled right after on the same client"
echo "     picked up its leftover checkpoint -- caught by verify_sample only after the fact."
echo "     The fix cross-checks a found checkpoint's range against the WU's own input file"
echo "     before trusting it, treating a mismatch exactly like no checkpoint at all.)"
cat > camicia_state <<EOF
$NEXT $START $END 0 0 0
EOF
cat > camicia_loops <<EOF
$MID 474 66
EOF
echo "0 1" > in
"$WORKER"
mismatch_loop_for_mid=$(count_loop_lines_for_mid out)
mismatch_finished_for_real_range=$(grep -cE "^finished,[01]," out || true)
echo "loop entries for MID after the mismatched-checkpoint run: $mismatch_loop_for_mid"
echo "finished entries for the real [0,1] range: $mismatch_finished_for_real_range"
rm -f in out camicia_state camicia_loops

echo
echo "=== CA-M2: fresh run with an out-of-range endIndex in \`in\` (MAX_INDEX + 1) ==="
echo "    (before the fix, an out-of-range index reaches getNthPermutation()'s live assert()"
echo "     and abort()s the process -- exit 134/SIGABRT -- instead of a clean boinc_finish(1),"
echo "     exit 1. Using && / || around the call, not a bare invocation, so a nonzero exit"
echo "     here doesn't trip this script's own \`set -e\`.)"
echo "0 653534134886878245000" > in
"$WORKER" && bad_range_rc=0 || bad_range_rc=$?
echo "exit code for out-of-range endIndex: $bad_range_rc"
rm -f in out camicia_state camicia_loops

echo
echo "=== CA-L6: checkpoint whose currentIndex is outside its own [start,end] ==="
echo "    (start/end in the checkpoint DO match this WU's \`in\` file, so the mismatched-"
echo "     checkpoint cross-check above doesn't catch this -- only currentIndex itself is bad.)"
BAD_CUR=$(python3 -c "print($END + 5)")
cat > camicia_state <<EOF
$BAD_CUR $START $END 0 0 0
EOF
echo "$START $END" > in
"$WORKER" && bad_current_rc=0 || bad_current_rc=$?
echo "exit code for out-of-range currentIndex: $bad_current_rc"
rm -f in out camicia_state camicia_loops

echo
echo "=== CA-L7: camicia_loops has more entries than this WU's range allows ==="
echo "    (checkpoint's start/end match the WU's own \`in\` file and currentIndex is in-range,"
echo "     so nothing else here rejects it -- only the loops-file entry count is bogus.)"
cat > camicia_state <<EOF
$START $START $END 0 0 0
EOF
python3 -c "
start = $START
for i in range(6):  # range is [START,END] = 5 deals; 6 lines exceeds the cap
    print(f'{start + i} 1 1')
" > camicia_loops
echo "$START $END" > in
"$WORKER" && too_many_loops_rc=0 || too_many_loops_rc=$?
echo "exit code for oversized camicia_loops: $too_many_loops_rc"
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

if [ "$mismatch_loop_for_mid" != "0" ]; then
    echo "❌ FAIL: a checkpoint claiming the [MID-2,MID+2] range was trusted even though this"
    echo "   WU's real input file says [0,1] -- MID isn't even in the real range, so any loop"
    echo "   claim for it here means the mismatched checkpoint's input-file cross-check regressed."
    pass=0
fi
if [ "$mismatch_finished_for_real_range" != "1" ]; then
    echo "❌ FAIL: expected exactly one 'finished' line for the real [0,1] input range after a"
    echo "   mismatched checkpoint is correctly discarded, got $mismatch_finished_for_real_range --"
    echo "   the worker isn't falling back to processing its own real input file."
    pass=0
fi

if [ "$bad_range_rc" = "1" ]; then
    echo "✅ CA-M2: out-of-range endIndex cleanly rejected (exit 1, no assert/abort)"
elif [ "$bad_range_rc" -gt 128 ] 2>/dev/null; then
    echo "❌ FAIL: out-of-range endIndex still aborts (exit $bad_range_rc, signal $((bad_range_rc-128)))"
    echo "   -- getNthPermutation()'s assert is still reachable; CA-M2 not fixed."
    pass=0
else
    echo "❌ FAIL: out-of-range endIndex should cleanly boinc_finish(1) (exit 1), got exit $bad_range_rc"
    pass=0
fi

if [ "$bad_current_rc" = "1" ]; then
    echo "✅ CA-L6: out-of-range currentIndex (checkpoint only) cleanly rejected (exit 1)"
elif [ "$bad_current_rc" -gt 128 ] 2>/dev/null; then
    echo "❌ FAIL: out-of-range currentIndex still aborts (exit $bad_current_rc, signal $((bad_current_rc-128)))"
    echo "   -- a checkpoint whose start/end match the WU but whose currentIndex doesn't is"
    echo "   still trusted; CA-L6 not fixed."
    pass=0
else
    echo "❌ FAIL: out-of-range currentIndex should cleanly boinc_finish(1) (exit 1), got exit $bad_current_rc"
    pass=0
fi

if [ "$too_many_loops_rc" = "1" ]; then
    echo "✅ CA-L7: oversized camicia_loops cleanly rejected (exit 1)"
elif [ "$too_many_loops_rc" -gt 128 ] 2>/dev/null; then
    echo "❌ FAIL: oversized camicia_loops crashes (exit $too_many_loops_rc, signal $((too_many_loops_rc-128)))"
    echo "   instead of cleanly boinc_finish(1)-ing; CA-L7 not fixed."
    pass=0
else
    echo "❌ FAIL: oversized camicia_loops should cleanly boinc_finish(1) (exit 1), got exit $too_many_loops_rc"
    pass=0
fi

if [ "$pass" = "1" ]; then
    echo "✅ PASS: current checkpoint semantics produce no duplicate under a normal resume,"
    echo "   the dedup guard correctly prevents a duplicate even when camicia_state and"
    echo "   camicia_loops are left inconsistent by a simulated crash between the two writes,"
    echo "   a genuinely orphaned camicia_loops file (camicia_state lost entirely) gets"
    echo "   truncated rather than duplicated across a real second resume, a checkpoint that"
    echo "   doesn't actually belong to this WU (range mismatch against its own input file) is"
    echo "   discarded rather than trusted, an out-of-range index -- whether the WU's own"
    echo "   endIndex or just a corrupted checkpoint currentIndex -- is cleanly rejected instead"
    echo "   of reaching getNthPermutation()'s assert (CA-M2/CA-L6), and a camicia_loops file with"
    echo "   more entries than the WU's own range allows is cleanly rejected too (CA-L7)."
    exit 0
else
    exit 1
fi
