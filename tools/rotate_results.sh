#!/bin/bash
# Rotates and gzips results/results.txt once it crosses ROTATE_THRESHOLD_BYTES,
# so the assimilator's single ever-appended output file doesn't grow forever
# unbounded. Rotated segments are gzip'd and kept FOREVER -- this is the
# project's actual scientific output (every game's card/trick count, needed
# to establish what "unusually long" even means, plus any loop finds), not
# disposable log data, so nothing here ever deletes a segment. Runs daily via
# config.xml's <tasks>, same pattern as disk_space_check.sh.
#
# Safe to rename out from under the assimilator: assimilator.cpp fopen()s/
# fclose()s results.txt fresh on every single assimilated result rather than
# holding it open for the daemon's lifetime, so the very next assimilation
# just creates a new file at that path -- no copytruncate/reopen dance needed.
set -e
cd "$(dirname "$0")/.."
. ./bin/notify.sh

ROTATE_THRESHOLD_BYTES=$((100 * 1024 * 1024))
RESULTS_FILE="results/results.txt"
ALERT_LOG="./results_rotate_alerts.log"

# Retry any segment left uncompressed by a previous run's failed gzip (see
# below) -- nothing else here ever revisits an already-rotated segment, so
# without this it would sit uncompressed forever instead of self-healing
# on the next daily run. Glob only matches rotated segments (results_<ts>.txt),
# never the live RESULTS_FILE itself (no underscore in "results.txt").
shopt -s nullglob
for LEFTOVER in results/results_*.txt; do
    if gzip "$LEFTOVER"; then
        MSG="OK: retry-gzip'd leftover $LEFTOVER from a previous failed rotation"
        echo "$(date '+%Y-%m-%d %H:%M:%S') $MSG"
        logger -t camicia_rotate_results "$MSG" 2>/dev/null || true
    else
        MSG="CRITICAL: retry-gzip of leftover $LEFTOVER is still failing"
        echo "$(date '+%Y-%m-%d %H:%M:%S') $MSG" | tee -a "$ALERT_LOG"
        logger -t camicia_rotate_results "$MSG" 2>/dev/null || true
        notify "Camicia: results rotation failed" "$MSG" "high"
    fi
done
shopt -u nullglob

if [ ! -f "$RESULTS_FILE" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') OK: no $RESULTS_FILE yet, nothing to rotate"
    exit 0
fi

SIZE=$(stat -c%s "$RESULTS_FILE")
if [ "$SIZE" -lt "$ROTATE_THRESHOLD_BYTES" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') OK: $RESULTS_FILE is ${SIZE} bytes, below the $((ROTATE_THRESHOLD_BYTES / 1024 / 1024))MB rotation threshold"
    exit 0
fi

TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
ROTATED="results/results_${TIMESTAMP}.txt"

if ! mv "$RESULTS_FILE" "$ROTATED"; then
    MSG="CRITICAL: failed to rotate $RESULTS_FILE (mv failed)"
    echo "$(date '+%Y-%m-%d %H:%M:%S') $MSG" | tee -a "$ALERT_LOG"
    logger -t camicia_rotate_results "$MSG" 2>/dev/null || true
    notify "Camicia: results rotation failed" "$MSG" "high"
    exit 1
fi

if ! gzip "$ROTATED"; then
    MSG="CRITICAL: rotated $RESULTS_FILE to $ROTATED but gzip failed -- uncompressed segment left in place, will retry on the next run"
    echo "$(date '+%Y-%m-%d %H:%M:%S') $MSG" | tee -a "$ALERT_LOG"
    logger -t camicia_rotate_results "$MSG" 2>/dev/null || true
    notify "Camicia: results rotation failed" "$MSG" "high"
    exit 1
fi

MSG="OK: rotated $RESULTS_FILE (${SIZE} bytes) to ${ROTATED}.gz"
echo "$(date '+%Y-%m-%d %H:%M:%S') $MSG"
logger -t camicia_rotate_results "$MSG" 2>/dev/null || true
