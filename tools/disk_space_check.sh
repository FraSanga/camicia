#!/bin/bash
# Warns (ntfy + syslog + a local alert log) when the filesystem backing the
# project directory is running low, since nothing else here bounds growth of
# results/results.txt, db_backups/, or BOINC's own logs against available
# disk space. Runs daily via config.xml's <tasks>, same pattern as
# db_backup.sh.
set -e
cd "$(dirname "$0")/.."
. ./bin/notify.sh

WARN_PCT=80
CRIT_PCT=90
ALERT_LOG="./disk_space_alerts.log"

USE_PCT=$(df -P . | awk 'NR==2 { gsub("%","",$5); print $5 }')
AVAIL=$(df -Ph . | awk 'NR==2 { print $4 }')

if [ "$USE_PCT" -ge "$CRIT_PCT" ]; then
    LEVEL="high"
    MSG="CRITICAL: disk usage at ${USE_PCT}% (${AVAIL} free) on $(pwd)"
elif [ "$USE_PCT" -ge "$WARN_PCT" ]; then
    LEVEL="default"
    MSG="WARNING: disk usage at ${USE_PCT}% (${AVAIL} free) on $(pwd)"
else
    MSG=""
fi

if [ -n "$MSG" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') $MSG" | tee -a "$ALERT_LOG"
    logger -t camicia_disk_space "$MSG" 2>/dev/null || true
    notify "Camicia: disk $LEVEL" "$MSG" "$LEVEL"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') OK: disk usage at ${USE_PCT}% (${AVAIL} free)"
fi
