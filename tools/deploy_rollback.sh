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
#
# If the failed run got as far as publishing a new app version before it
# broke, a plain file revert above isn't enough -- that version is already
# live and immutable, so --restore additionally republishes the
# just-restored (old, known-good) code as a fresh, higher version number,
# and explicitly deprecates the broken one (tools/publish_version.sh,
# html/ops/deprecate_app_version.php). See publish_version.sh's own header
# for why that logic lives there and not duplicated here.
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
# It's relative to the repo root (same convention docker-compose.yml itself
# relies on, since that's always invoked from there) -- but this script's
# own cwd is tools/ (the `cd "$(dirname "$0")"` above), so it's resolved
# against the actual repo root rather than joined onto cwd directly.
REPO_ROOT="$(cd .. && pwd)"
HOST_PROJECT_DIR="$REPO_ROOT/${SERVER_VOLUME_PROJECTS#./}/camicia"
CONTAINER_PROJECT_DIR="${SERVER_VOLUME_PROJECTS_DIR}/camicia"
BACKUP_DIR="$HOME/.camicia_deploy_backups"
BACKUP_FILE="$BACKUP_DIR/pre_deploy.tar.gz"
# Written by publish_version.sh the instant it publishes a new app
# version (the true point of no return -- see that script's own
# comment), read here to decide whether a plain file revert is enough or
# a replacement version also needs publishing. Cleared at the start of
# every --backup (a fresh deploy attempt) so a stale marker from a run
# that already finished (successfully or via its own completed --restore)
# never leaks into a later, unrelated failure.
PUBLISHED_VERSION_FILE="$BACKUP_DIR/published_version"
DEPLOYED_SHA_FILE="$HOME/.camicia_deploy_state/deployed_sha"
# Same subpaths tools.sh's docker cp block overwrites -- kept in sync
# manually with that list. Deliberately not results/, db_backups/, apps/,
# download/ -- see header comment above.
BACKUP_PATHS="assimilator worker work_generator templates html bin config.xml project.xml terms_of_use.txt"

do_backup() {
    mkdir -p "$BACKUP_DIR"
    rm -f "$PUBLISHED_VERSION_FILE"
    # --ignore-failed-read: some paths may not exist yet on a brand new
    # project -- keep going instead of aborting the whole backup over one
    # missing entry.
    #
    # --exclude=html/ops/.htpasswd: this one file is 640, owned by
    # www-data -- unreadable by the runner's own host user (confirmed:
    # `ls -la` shows `-rw-r----- www-data www-data`), so it was already
    # silently dropped from every backup via --ignore-failed-read, just
    # with a "Permission denied" warning on every single deploy. Excluding
    # it outright is a deliberate no-op, not a new gap: nothing in the
    # deploy pipeline ever writes or deletes .htpasswd (it's a manually-set
    # ops admin password), so --restore never needed to touch it -- the
    # live file was never at risk either way, this just makes the exclusion
    # explicit and silent instead of an unexplained warning every time.
    tar czf "$BACKUP_FILE.tmp" --ignore-failed-read --exclude='html/ops/.htpasswd' -C "$HOST_PROJECT_DIR" $BACKUP_PATHS
    mv "$BACKUP_FILE.tmp" "$BACKUP_FILE"
    echo "✅ Pre-deploy backup saved to $BACKUP_FILE"
}

do_restore() {
    if [ ! -f "$BACKUP_FILE" ]; then
        echo "❌ No backup found at $BACKUP_FILE -- nothing to restore."
        notify "Camicia: rollback FAILED" "No pre-deploy backup found at $BACKUP_FILE -- manual recovery needed" "high"
        return 1
    fi

    local old_sha="unknown"
    [ -f "$DEPLOYED_SHA_FILE" ] && old_sha=$(cat "$DEPLOYED_SHA_FILE")
    local stage_note=""
    [ -n "$CURRENT_STAGE" ] && stage_note=" (failed during '$CURRENT_STAGE')"

    echo "⏪ Restoring pre-deploy state from $BACKUP_FILE..."
    tar xzf "$BACKUP_FILE" -C "$HOST_PROJECT_DIR"

    local bad_version=""
    [ -f "$PUBLISHED_VERSION_FILE" ] && bad_version=$(cat "$PUBLISHED_VERSION_FILE")

    if [ -n "$bad_version" ]; then
        echo "⚠️  A broken app version ($bad_version) was published before this run failed -- publishing a replacement..."
        # --force: publish_version.sh's own git-diff-based "did the worker
        # source actually change" gate can't see this. The tar extract
        # above just restored worker/ files into the *container* from the
        # pre-deploy backup -- local git HEAD hasn't moved, so an
        # unconditional call here would diff HEAD against itself, see no
        # change, and wrongly skip republishing the very known-good binary
        # this path exists to restage.
        if bash ./publish_version.sh --force; then
            local good_version=""
            [ -f "$PUBLISHED_VERSION_FILE" ] && good_version=$(cat "$PUBLISHED_VERSION_FILE")

            echo "🚫 Deprecating broken version $bad_version..."
            local deprecate_note
            if docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c \
                "cd $CONTAINER_PROJECT_DIR/html/ops && ./deprecate_app_version.php '$bad_version'"; then
                deprecate_note="version $bad_version deprecated"
            else
                deprecate_note="⚠️ FAILED to deprecate version $bad_version -- it may still be resent to hosts that already have it, deprecate manually via html/ops/manage_app_versions.php or: docker exec --user $PROJECTS_USER $SERVER_CONTAINER_NAME bash -c 'cd $CONTAINER_PROJECT_DIR/html/ops && ./deprecate_app_version.php $bad_version'"
            fi

            rm -f "$PUBLISHED_VERSION_FILE"

            if docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "cd $CONTAINER_PROJECT_DIR && ./bin/start"; then
                echo "✅ Rolled back to $old_sha, published $good_version to replace broken $bad_version, daemons restarted."
                notify "Camicia: deploy rolled back + republished" "Restored code from $old_sha$stage_note. Published version $good_version to replace broken $bad_version ($deprecate_note). Daemons restarted." "default"
            else
                echo "⚠️  Republished but daemons did not restart."
                notify "Camicia: rollback PARTIAL" "Restored $old_sha and published $good_version replacing $bad_version ($deprecate_note), but daemons did not restart -- needs manual intervention" "high"
                return 1
            fi
        else
            echo "❌ Failed to publish a replacement version -- broken version $bad_version is still live and NOT deprecated."
            if docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "cd $CONTAINER_PROJECT_DIR && ./bin/start"; then
                notify "Camicia: rollback INCOMPLETE" "Restored source from $old_sha$stage_note, but publishing a replacement for broken version $bad_version FAILED -- that broken version is still live and being served. Needs manual intervention: check tools/publish_version.sh's output and rerun, or deprecate $bad_version manually." "high"
            else
                notify "Camicia: rollback INCOMPLETE, daemons DOWN" "Restored source from $old_sha$stage_note, publishing a replacement for broken version $bad_version FAILED, and daemons did not restart -- needs urgent manual intervention." "high"
            fi
            return 1
        fi
    else
        if docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "cd $CONTAINER_PROJECT_DIR && ./bin/start"; then
            echo "✅ Rolled back to $old_sha and daemons restarted."
            notify "Camicia: deploy rolled back" "Restored pre-deploy files (back to $old_sha)$stage_note and restarted daemons. No app version had been published yet, nothing else to undo." "default"
        else
            echo "⚠️  Files restored but daemons did not restart."
            notify "Camicia: rollback PARTIAL" "Restored $old_sha$stage_note but daemons did not restart -- needs manual intervention" "high"
            return 1
        fi
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
