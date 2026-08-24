#!/bin/bash
# Rotates BOINC's own daemon debug logs (log_<hostname>/*.log -- assimilator,
# feeder, transitioner, work_generator, sample_bitwise_validator,
# file_deleter, all run at -d 3) once a log crosses ROTATE_THRESHOLD_BYTES.
# Unlike rotate_results.sh, these ARE disposable diagnostic output: rotated
# segments are gzip'd and kept for the newest RETAIN_COUNT rotations per log,
# then deleted -- same retention style as db_backup.sh, not the "keep
# forever" policy results.txt needs.
#
# Uses copytruncate (copy the log's current content out, then truncate the
# file IN PLACE to 0 bytes) rather than rename. bin/start's redirect()
# open()s each daemon's log once, in append mode, at daemon startup, and
# dup2()s it onto the daemon's stdout/stderr for the daemon's entire
# lifetime -- it's never reopened. Renaming would orphan the daemon's
# already-open fd from the visible filename forever (nothing would ever
# appear at the original path again until the daemon restarts). Truncating
# in place keeps the same inode/fd valid, and because the fd was opened in
# append mode, every subsequent write() atomically seeks to the (now empty)
# end of file first -- safe even mid-write, no signal-to-reopen or restart
# needed. This is the standard logrotate "copytruncate" technique, used for
# exactly this class of daemon.
set -e
cd "$(dirname "$0")/.."
. ./bin/notify.sh

ROTATE_THRESHOLD_BYTES=$((20 * 1024 * 1024))
RETAIN_COUNT=14
# SERVER_HOSTNAME isn't necessarily propagated into cron's job environment
# (cron gives jobs a minimal environment, not docker-compose's), so fall
# back to the container's own hostname, which is set to the same value.
LOG_DIR="log_${SERVER_HOSTNAME:-$(hostname)}"
ALERT_LOG="./daemon_log_rotate_alerts.log"

if [ ! -d "$LOG_DIR" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') OK: no $LOG_DIR yet, nothing to rotate"
    exit 0
fi

ROTATED_ANY=0
for LOGFILE in "$LOG_DIR"/*.log; do
    [ -e "$LOGFILE" ] || continue
    BASENAME=$(basename "$LOGFILE" .log)
    SIZE=$(stat -c%s "$LOGFILE")
    if [ "$SIZE" -lt "$ROTATE_THRESHOLD_BYTES" ]; then
        continue
    fi

    TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
    ROTATED="$LOG_DIR/${BASENAME}_${TIMESTAMP}.log"

    if ! cp "$LOGFILE" "$ROTATED"; then
        MSG="CRITICAL: failed to copy $LOGFILE for rotation"
        echo "$(date '+%Y-%m-%d %H:%M:%S') $MSG" | tee -a "$ALERT_LOG"
        logger -t camicia_rotate_daemon_logs "$MSG" 2>/dev/null || true
        notify "Camicia: daemon log rotation failed" "$MSG" "high"
        continue
    fi
    : > "$LOGFILE"
    if ! gzip "$ROTATED"; then
        MSG="CRITICAL: rotated $LOGFILE to $ROTATED but gzip failed -- uncompressed segment left in place"
        echo "$(date '+%Y-%m-%d %H:%M:%S') $MSG" | tee -a "$ALERT_LOG"
        logger -t camicia_rotate_daemon_logs "$MSG" 2>/dev/null || true
        notify "Camicia: daemon log rotation failed" "$MSG" "high"
        continue
    fi

    ROTATED_ANY=1
    MSG="rotated $LOGFILE (${SIZE} bytes) to ${ROTATED}.gz"
    echo "$(date '+%Y-%m-%d %H:%M:%S') OK: $MSG"
    logger -t camicia_rotate_daemon_logs "$MSG" 2>/dev/null || true

    # retention: keep the newest RETAIN_COUNT rotated segments for this log
    ls -1t "$LOG_DIR/${BASENAME}"_*.log.gz 2>/dev/null | tail -n +$((RETAIN_COUNT + 1)) | xargs -r rm -f
done

if [ "$ROTATED_ANY" -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') OK: no daemon logs above $((ROTATE_THRESHOLD_BYTES / 1024 / 1024))MB"
fi
