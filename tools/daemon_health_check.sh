#!/bin/bash
# Closes the gap between deploy.yml's one-time post-deploy health check and
# whatever happens to a daemon days/weeks later with no new deploy: checks
# the same 6 core BOINC daemons (config.xml's <daemons>) are running, and if
# not, tries bin/start (BOINC skips daemons already running via their pid
# files, so this only starts what's actually down -- safe to call anytime).
# Always notifies either way, even when the restart succeeds: a daemon that
# keeps dying and quietly getting restarted is still a symptom worth
# knowing about, not something to stay silent on just because it self-healed.
# Runs periodically via config.xml's <tasks>, same pattern as
# disk_space_check.sh/memory_check.sh.
set -e
cd "$(dirname "$0")/.."
. ./bin/notify.sh

DAEMONS="assimilator sample_bitwise_validator work_generator feeder transitioner file_deleter"
ALERT_LOG="./daemon_health_alerts.log"

missing_daemons() {
    local out=""
    for d in $DAEMONS; do
        pgrep -f "^$d " >/dev/null 2>&1 || out="$out $d"
    done
    echo "$out"
}

BEFORE=$(missing_daemons)
if [ -z "$BEFORE" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') OK: all daemons running"
    exit 0
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') Missing:$BEFORE -- attempting restart via bin/start" | tee -a "$ALERT_LOG"
logger -t camicia_daemon_health "Missing:$BEFORE -- attempting restart" 2>/dev/null || true
./bin/start >/dev/null 2>&1 || true

AFTER=$(missing_daemons)
if [ -z "$AFTER" ]; then
    notify "Camicia: daemon(s) restarted" "Were down:$BEFORE -- back up after bin/start" "default"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') Still missing after restart attempt:$AFTER" | tee -a "$ALERT_LOG"
    notify "Camicia: daemon(s) DOWN" "Still missing after restart attempt:$AFTER -- needs manual intervention" "high"
fi
