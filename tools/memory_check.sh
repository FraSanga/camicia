#!/bin/bash
# Warns (ntfy + syslog + a local alert log) as the host approaches the
# thresholds where earlyoom starts killing processes (see DEPLOYMENT.md
# SS4), so there's a heads-up before that happens instead of only finding
# out via `journalctl -u earlyoom` after the fact. Runs daily via
# config.xml's <tasks>, same pattern as disk_space_check.sh. Reads
# host-wide memory stats, which is what `free`/`/proc/meminfo` report even
# from inside this container (not namespaced), matching what earlyoom
# itself sees on the host.
set -e
cd "$(dirname "$0")/.."
. ./bin/notify.sh

WARN_PCT=20
CRIT_PCT=10
ALERT_LOG="./memory_alerts.log"

read -r MEM_AVAIL_PCT SWAP_FREE_PCT <<< "$(free | awk '
    /^Mem:/  { mem = $7/$2*100 }
    /^Swap:/ { swap = ($2 > 0) ? $4/$2*100 : 100 }
    END      { printf "%.0f %.0f", mem, swap }
')"

if [ "$MEM_AVAIL_PCT" -le "$CRIT_PCT" ] && [ "$SWAP_FREE_PCT" -le "$CRIT_PCT" ]; then
    LEVEL="high"
    MSG="CRITICAL: mem ${MEM_AVAIL_PCT}% available, swap ${SWAP_FREE_PCT}% free -- earlyoom will act soon if this holds"
elif [ "$MEM_AVAIL_PCT" -le "$WARN_PCT" ]; then
    LEVEL="default"
    MSG="WARNING: mem ${MEM_AVAIL_PCT}% available, swap ${SWAP_FREE_PCT}% free"
else
    MSG=""
fi

if [ -n "$MSG" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') $MSG" | tee -a "$ALERT_LOG"
    logger -t camicia_memory "$MSG" 2>/dev/null || true
    notify "Camicia: memory $LEVEL" "$MSG" "$LEVEL"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') OK: mem ${MEM_AVAIL_PCT}% available, swap ${SWAP_FREE_PCT}% free"
fi
