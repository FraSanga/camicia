#!/bin/bash
# Pushes keys/, db_backups/, results/, and archives/ to Google Drive via rclone -- genuine
# offsite protection, unlike db_backups/ and results/ segments, which
# otherwise only ever live on this one host's disk. Runs daily via
# config.xml's <tasks>, same pattern as db_backup.sh/rotate_results.sh.
#
# No-ops cleanly if keys/rclone.conf or keys/rclone_config_pass aren't
# present -- true on every local dev deploy, since there's no local-dev
# equivalent of this backup (local gets torn down/rebuilt regularly,
# nothing there is worth backing up offsite).
#
# Unlike CODE_SIGN_KEY_PASSPHRASE (only ever needed transiently, during a
# deploy.yml run, for update_versions), this script runs independently every
# day via bin/start --cron, long after any deploy.yml process has exited --
# so tools.sh writes the passphrase to keys/rclone_config_pass (from the
# `production`-Environment GitHub Actions secret RCLONE_CONFIG_PASS, never
# to any .env) rather than relying on an env var that cron has no way to
# still have set days later.
set -e
cd "$(dirname "$0")/.."
. ./bin/notify.sh

# $HOME/keys, not a relative path off the project dir: keys/ is a separate
# bind mount (SERVER_VOLUME_KEYS, independent of SERVER_VOLUME_PROJECTS) --
# $HOME is the one thing cron reliably sets correctly for the user's own
# jobs regardless of what environment variables cron itself does or doesn't
# inherit, whereas SERVER_VOLUME_KEYS_DIR is a docker-compose-level env var
# with no guarantee of surviving into cron's job environment.
KEYS_DIR="$HOME/keys"
RCLONE_CONF="$KEYS_DIR/rclone.conf"
PASS_FILE="$KEYS_DIR/rclone_config_pass"
REMOTE="camicia-gdrive:camicia-backup"

if [ ! -f "$RCLONE_CONF" ] || [ ! -f "$PASS_FILE" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') SKIP: rclone.conf or rclone_config_pass not present (expected on local dev)"
    exit 0
fi
RCLONE_CONFIG_PASS="$(<"$PASS_FILE")"
export RCLONE_CONFIG_PASS

fail() {
    local msg="$1"
    echo "$(date '+%Y-%m-%d %H:%M:%S') FAILED: $msg"
    notify "Camicia: offsite backup failed" "$msg" high
    exit 1
}

# keys/ is small and low-churn -- mirror it exactly. Exclude rclone.conf
# AND rclone_config_pass: no reason to upload the credential (or the
# plaintext passphrase that decrypts it) to the same Drive account they
# grant access to. Also exclude the plaintext "upload_private" specifically:
# unlike code_sign_private, it has to stay decrypted on disk continuously
# (transitioner reads it unconditionally at startup -- see tools.sh's own
# comment), so only its encrypted upload_private.gpg counterpart should ever
# leave the host; the live plaintext copy is local-only, by design.
rclone sync "$KEYS_DIR" "$REMOTE/keys" --config "$RCLONE_CONF" \
    --exclude rclone.conf --exclude rclone_config_pass --exclude upload_private \
    || fail "rclone sync of keys/ failed"

# db_backups/ and results/ are additive (copy, not sync): db_backup.sh
# already prunes db_backups/ to the newest 7 locally, and rotate_results.sh
# never deletes results/ segments either -- Drive becomes the longer-term
# archive, retaining whatever local has already rotated/pruned away rather
# than mirroring local's own retention window.
rclone copy ./db_backups "$REMOTE/db_backups" --config "$RCLONE_CONF" \
    || fail "rclone copy of db_backups/ failed"

rclone copy ./results "$REMOTE/results" --config "$RCLONE_CONF" \
    || fail "rclone copy of results/ failed"

# archives/ (db_purge --gzip's row-metadata dump, see config.xml's own <task> comment): additive
# for the same reason as db_backups/results above, nothing here ever deletes an archive locally
# either.
rclone copy ./archives "$REMOTE/archives" --config "$RCLONE_CONF" \
    || fail "rclone copy of archives/ failed"

echo "$(date '+%Y-%m-%d %H:%M:%S') OK: offsite backup to Google Drive complete"
