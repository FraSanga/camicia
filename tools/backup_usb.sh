#!/bin/bash
# Mirrors keys/, db_backups/, and results/ to a second, physically separate
# local drive (BACKUP_DRIVE_DIR) -- a genuine second copy independent of the
# host's main disk, distinct from the Google Drive offsite backup
# (backup_offsite_gdrive.sh) and from SERVER_VOLUME_PROJECTS/KEYS
# themselves. Runs daily via config.xml's <tasks>, same pattern as every
# other backup/maintenance script here.
#
# No drive-detection logic needed: on local dev, BACKUP_DRIVE_DIR points at
# a throwaway /tmp stub (docker-compose.yml's own default) -- mirroring
# local dev's own small test data into a scratch dir on the same machine is
# harmless, so this script doesn't need to distinguish "real drive" from
# "stub" the way the offsite backup needs to distinguish "have credentials"
# from "don't".
set -e
cd "$(dirname "$0")/.."
. ./bin/notify.sh

KEYS_DIR="$HOME/keys"
DEST="${BACKUP_DRIVE_DIR:-/mnt/backup1tb}"

if [ ! -d "$DEST" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') SKIP: $DEST not present"
    exit 0
fi

fail() {
    local msg="$1"
    echo "$(date '+%Y-%m-%d %H:%M:%S') FAILED: $msg"
    notify "Camicia: USB backup failed" "$msg" high
    exit 1
}

# Full mirror (--delete), unlike the offsite Google Drive backup's
# additive copy -- this is a second *local* copy of the same current state,
# not a longer-term archive, so there's no reason to keep stale data around
# once the primary copy has rotated/pruned it away.
mkdir -p "$DEST/keys" "$DEST/db_backups" "$DEST/results"
# Exclude rclone.conf, rclone_config_pass, AND the plaintext upload_private
# -- same reasoning as backup_offsite_gdrive.sh's own exclude list, and it
# applies just as much to a physically separate, removable drive as it does
# to a remote one: keeping these off of every secondary copy means a single
# stolen backup (this drive, the Drive account, whichever) never hands over
# a ready-to-use credential or plaintext key on its own. upload_private.gpg
# (the encrypted counterpart) is still included -- only the live plaintext
# copy is excluded.
rsync -a --delete --exclude rclone.conf --exclude rclone_config_pass --exclude upload_private \
    "$KEYS_DIR/" "$DEST/keys/" || fail "rsync of keys/ failed"
rsync -a --delete ./db_backups/ "$DEST/db_backups/" || fail "rsync of db_backups/ failed"
rsync -a --delete ./results/ "$DEST/results/" || fail "rsync of results/ failed"

echo "$(date '+%Y-%m-%d %H:%M:%S') OK: USB backup complete"
