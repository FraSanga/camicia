#!/bin/bash
# Shared by disk_space_check.sh and memory_check.sh. Sends a push
# notification via ntfy.sh if a topic has been configured (deployed to
# ./ntfy_topic by tools.sh, from NTFY_TOPIC in .env). Silently does nothing
# if not configured, so the checks still work before ntfy is set up.
notify() {
    local title="$1" message="$2" priority="${3:-default}"
    [ -f ./ntfy_topic ] || return 0
    local topic
    topic=$(<./ntfy_topic)
    [ -n "$topic" ] || return 0
    curl -fsS \
        -H "Title: $title" \
        -H "Priority: $priority" \
        -H "Tags: warning" \
        -d "$message" \
        "https://ntfy.sh/$topic" >/dev/null 2>&1 || true
}
