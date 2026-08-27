#!/bin/bash
# Shared by disk_space_check.sh, memory_check.sh, tools.sh, and
# deploy_rollback.sh. Sends a push notification via ntfy.sh if a topic has
# been configured. Two different callers, two different ways of knowing the
# topic: disk_space_check.sh/memory_check.sh run *inside* the container via
# a cron-triggered config.xml <task>, with none of .env's variables in their
# environment, so they need ./ntfy_topic (deployed to the container by
# tools.sh, from NTFY_TOPIC in .env). tools.sh/deploy_rollback.sh run on the
# *host* runner instead, where that file was never written -- but both
# already `. ../.env` themselves, so $NTFY_TOPIC is already a live
# exported variable there. Check the env var first, then fall back to the
# file, so one notify() works correctly from either context. Silently does
# nothing if neither is set, so the checks still work before ntfy is set up.
notify() {
    local title="$1" message="$2" priority="${3:-default}"
    local topic="$NTFY_TOPIC"
    if [ -z "$topic" ] && [ -f ./ntfy_topic ]; then
        topic=$(<./ntfy_topic)
    fi
    [ -n "$topic" ] || return 0
    curl -fsS \
        -H "Title: $title" \
        -H "Priority: $priority" \
        -H "Tags: warning" \
        -d "$message" \
        "https://ntfy.sh/$topic" >/dev/null 2>&1 || true
}
