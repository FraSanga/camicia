#!/bin/bash
# Host-side (runner) backup/restore for tools.sh's destructive deploy
# stages. Unlike every other script in tools/, this one is never copied
# into the container -- it's invoked directly by tools.sh and deploy.yml,
# which both already run on the runner itself.
#
# --backup: tars up the specific PROJECT_DIR subpaths tools.sh is about to
# overwrite into a single, always-overwritten file outside the bind mount
# (so it survives the very overwrite it's protecting against). Excludes
# results/, db_backups/, apps/, download/ -- huge/always-growing, or (for
# apps//download/) explicitly out of scope: BOINC treats a published app
# version as immutable, so reverting it blindly risks a worse
# inconsistency than the failure being recovered from. A broken version
# gets fixed by publishing a new one, not by undoing the old one.
#
# --restore: extracts that same tar back over PROJECT_DIR and restarts the
# daemons, notifying either way (tools/notify.sh). Called from two places:
# tools.sh's own failure trap (a deploy that fails partway), and
# deploy.yml's post-health-check failure step (a deploy that succeeded but
# left the project unhealthy).
set -e
cd "$(dirname "$0")"

if [ -f ../.env ]; then
    set -a
    . ../.env
    set +a
else
    echo "❌ ERROR: ../.env file not found!"
    exit 1
fi

. ./notify.sh

# SERVER_VOLUME_PROJECTS is the host-side path docker-compose.yml binds to
# SERVER_VOLUME_PROJECTS_DIR inside the container -- this script runs on
# the host, so it operates on the former directly, no docker exec needed.
HOST_PROJECT_DIR="${SERVER_VOLUME_PROJECTS}/camicia"
CONTAINER_PROJECT_DIR="${SERVER_VOLUME_PROJECTS_DIR}/camicia"
BACKUP_DIR="$HOME/.camicia_deploy_backups"
BACKUP_FILE="$BACKUP_DIR/pre_deploy.tar.gz"
# Same subpaths tools.sh's docker cp block overwrites -- kept in sync
# manually with that list. Deliberately not results/, db_backups/, apps/,
# download/ -- see header comment above.
BACKUP_PATHS="assimilator worker work_generator templates html bin config.xml project.xml terms_of_use.txt"

do_backup() {
    mkdir -p "$BACKUP_DIR"
    # --ignore-failed-read: some paths may not exist yet on a brand new
    # project -- keep going instead of aborting the whole backup over one
    # missing entry.
    tar czf "$BACKUP_FILE.tmp" --ignore-failed-read -C "$HOST_PROJECT_DIR" $BACKUP_PATHS
    mv "$BACKUP_FILE.tmp" "$BACKUP_FILE"
    echo "✅ Pre-deploy backup saved to $BACKUP_FILE"
}

do_restore() {
    if [ ! -f "$BACKUP_FILE" ]; then
        echo "❌ No backup found at $BACKUP_FILE -- nothing to restore."
        notify "Camicia: rollback FAILED" "No pre-deploy backup found at $BACKUP_FILE -- manual recovery needed" "high"
        return 1
    fi

    echo "⏪ Restoring pre-deploy state from $BACKUP_FILE..."
    tar xzf "$BACKUP_FILE" -C "$HOST_PROJECT_DIR"

    echo "🔄 Restarting daemons..."
    if docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "cd $CONTAINER_PROJECT_DIR && ./bin/start"; then
        echo "✅ Rolled back and daemons restarted."
        notify "Camicia: deploy rolled back" "Restored pre-deploy files and restarted daemons after a failed deploy/health check" "default"
    else
        echo "⚠️  Files restored but daemons did not restart."
        notify "Camicia: rollback PARTIAL" "Files restored but daemons did not restart -- needs manual intervention" "high"
        return 1
    fi
}

case "$1" in
    --backup) do_backup ;;
    --restore) do_restore ;;
    *)
        echo "Usage: $0 --backup|--restore"
        exit 1
        ;;
esac
