#!/bin/bash
set -euo pipefail

# tools/discord_bot_control.sh
# Manages Discord Bot lifecycle with mutual exclusion between Production and Staging.
# Hierarchy rule: Production takes precedence over Staging.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PROD_CONTAINER="boinc_server_discord_bot"
STAGING_CONTAINER="boinc_server_staging_discord_bot"

PROD_DIR="${PROD_DIR:-$HOME/camicia}"
STAGING_DIR="${STAGING_DIR:-$HOME/camicia-staging}"

is_running() {
    local container="$1"
    [ "$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || true)" = "true" ]
}

get_target_dir() {
    local preferred="$1"
    if [ -d "$preferred" ] && [ -f "$preferred/docker-compose.yml" ]; then
        echo "$preferred"
    elif [ -f "$REPO_ROOT/docker-compose.yml" ]; then
        echo "$REPO_ROOT"
    else
        echo "$preferred"
    fi
}

start_production() {
    local build_flag=""
    if [ "${1:-}" = "--build" ] || [ "${2:-}" = "--build" ]; then
        build_flag="--build"
    fi

    echo "=== Starting CamiciaBot (Production) ==="

    if is_running "$STAGING_CONTAINER"; then
        echo "⚠️  CamiciaBot Staging is currently running. Production takes priority!"
        echo "🛑 Shutting down CamiciaBot Staging ($STAGING_CONTAINER)..."
        docker stop "$STAGING_CONTAINER" 2>/dev/null || true
    fi

    local target_dir
    target_dir="$(get_target_dir "$PROD_DIR")"
    cd "$target_dir"

    echo "🚀 Launching $PROD_CONTAINER (dir: $target_dir, build: ${build_flag:-none})..."
    docker compose -p camicia --profile discord up -d $build_flag discord_bot

    if is_running "$PROD_CONTAINER"; then
        echo "✅ CamiciaBot (Production) is active and running."
    else
        echo "⚠️  Container launch command completed, but $PROD_CONTAINER is not running yet."
    fi
}

start_staging() {
    local build_flag=""
    if [ "${1:-}" = "--build" ] || [ "${2:-}" = "--build" ]; then
        build_flag="--build"
    fi

    echo "=== Starting CamiciaBot Staging ==="

    if is_running "$PROD_CONTAINER"; then
        echo "⛔ Cannot start CamiciaBot Staging: CamiciaBot (Production) is currently RUNNING."
        echo "   To avoid confusion or duplicate slash commands, Staging bot cannot run concurrently."
        echo "   (If you wish to test Staging, shut down Production bot first with: $0 stop-production)"
        exit 0
    fi

    local target_dir
    target_dir="$(get_target_dir "$STAGING_DIR")"
    cd "$target_dir"

    echo "🚀 Launching $STAGING_CONTAINER (dir: $target_dir, build: ${build_flag:-none})..."
    docker compose -p camicia-staging --profile discord up -d $build_flag discord_bot

    if is_running "$STAGING_CONTAINER"; then
        echo "✅ CamiciaBot Staging is active and running."
    else
        echo "⚠️  Container launch command completed, but $STAGING_CONTAINER is not running yet."
    fi
}

stop_production() {
    echo "=== Stopping CamiciaBot (Production) ==="
    if is_running "$PROD_CONTAINER"; then
        echo "🛑 Stopping container $PROD_CONTAINER..."
        docker stop "$PROD_CONTAINER" 2>/dev/null || true
        echo "✅ CamiciaBot (Production) stopped."
    else
        echo "ℹ️  CamiciaBot (Production) is not running."
    fi
}

stop_staging() {
    echo "=== Stopping CamiciaBot Staging ==="
    if is_running "$STAGING_CONTAINER"; then
        echo "🛑 Stopping container $STAGING_CONTAINER..."
        docker stop "$STAGING_CONTAINER" 2>/dev/null || true
        echo "✅ CamiciaBot Staging stopped."
    else
        echo "ℹ️  CamiciaBot Staging is not running."
    fi
}

show_status() {
    echo "=========================================="
    echo "        Camicia Discord Bots Status       "
    echo "=========================================="
    local prod_status="STOPPED"
    local staging_status="STOPPED"

    if is_running "$PROD_CONTAINER"; then
        prod_status="RUNNING (Active)"
    fi
    if is_running "$STAGING_CONTAINER"; then
        staging_status="RUNNING (Active)"
    fi

    echo "Production Bot ($PROD_CONTAINER):         $prod_status"
    echo "Staging Bot    ($STAGING_CONTAINER): $staging_status"
    echo "=========================================="
}

ACTION="${1:-status}"
shift || true

case "$ACTION" in
    start-production)
        start_production "$@"
        ;;
    start-staging)
        start_staging "$@"
        ;;
    stop-production)
        stop_production
        ;;
    stop-staging)
        stop_staging
        ;;
    status)
        show_status
        ;;
    *)
        echo "Usage: $0 {start-production|start-staging|stop-production|stop-staging|status} [--build]"
        exit 1
        ;;
esac
