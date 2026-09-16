#!/bin/bash
# Mirrors keys/, db_backups/, results/, and archives/ to physically separate
# local drives (BACKUP_DRIVE_DIR and BACKUP_STORAGE_DIR) -- genuine local copies
# independent of the host's main disk, distinct from the Google Drive offsite
# backup (backup_offsite_gdrive.sh) and from SERVER_VOLUME_PROJECTS/KEYS
# themselves. Runs daily via config.xml's <tasks>, same pattern as every
# other backup/maintenance script here.
#
# Completes a 3-2-1 backup strategy:
#   - 3 copies of data (Live SSD, Local HDD 1, Local HDD 2, plus Offsite Google Drive)
#   - 2 independent local media (Toshiba 1TB HDD and Seagate 500GB HDD)
#   - 1 offsite copy (Google Drive)
#
# No drive-detection logic needed: on local dev, BACKUP_DRIVE_DIR and
# BACKUP_STORAGE_DIR point at throwaway /tmp stubs (docker-compose.yml's own
# defaults) -- mirroring local dev's own small test data into a scratch dir on
# the same machine is harmless. Missing or unmounted destinations are cleanly
# skipped unless explicitly present and writable.
set -e
cd "$(dirname "$0")/.."
. ./bin/notify.sh

KEYS_DIR="$HOME/keys"

fail() {
    local msg="$1"
    echo "$(date '+%Y-%m-%d %H:%M:%S') FAILED: $msg"
    notify "Camicia: USB backup failed" "$msg" high
    exit 1
}

# Collect configured, present, and writable backup destinations
DESTS=()
for candidate in "${BACKUP_DRIVE_DIR:-/mnt/backup1tb}" "${BACKUP_STORAGE_DIR:-/mnt/storage500gb}"; do
    [ -z "$candidate" ] && continue
    # Deduplicate in case both variables point to the same directory
    for existing in "${DESTS[@]}"; do
        [ "$existing" = "$candidate" ] && continue 2
    done
    if [ ! -d "$candidate" ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') SKIP: $candidate not present"
        continue
    fi
    if [ ! -w "$candidate" ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') SKIP: $candidate not writable"
        continue
    fi
    DESTS+=("$candidate")
done

if [ ${#DESTS[@]} -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') SKIP: No writable backup destinations present"
    exit 0
fi

for DEST in "${DESTS[@]}"; do
    echo "$(date '+%Y-%m-%d %H:%M:%S') Starting backup to $DEST..."
    # Full mirror (--delete), unlike the offsite Google Drive backup's
    # additive copy -- this is a local copy of the same current state,
    # not a longer-term archive, so there's no reason to keep stale data around
    # once the primary copy has rotated/pruned it away.
    mkdir -p "$DEST/keys" "$DEST/db_backups" "$DEST/results" "$DEST/archives"
    # Exclude rclone.conf, rclone_config_pass, AND the plaintext upload_private
    # -- same reasoning as backup_offsite_gdrive.sh's own exclude list, and it
    # applies just as much to a physically separate, removable drive as it does
    # to a remote one: keeping these off of every secondary copy means a single
    # stolen backup (this drive, the Drive account, whichever) never hands over
    # a ready-to-use credential or plaintext key on its own. upload_private.gpg
    # (the encrypted counterpart) is still included -- only the live plaintext
    # copy is excluded.
    rsync -a --delete --exclude rclone.conf --exclude rclone_config_pass --exclude upload_private \
        "$KEYS_DIR/" "$DEST/keys/" || fail "rsync of keys/ to $DEST failed"
    rsync -a --delete ./db_backups/ "$DEST/db_backups/" || fail "rsync of db_backups/ to $DEST failed"
    rsync -a --delete ./results/ "$DEST/results/" || fail "rsync of results/ to $DEST failed"
    # archives/ (db_purge --gzip's row-metadata dump, see config.xml's own <task> comment): nothing
    # reads it back at runtime, in this project or in BOINC's own source, so unlike results/ it's safe
    # to prune locally in a genuine disk emergency once this copy exists.
    rsync -a --delete ./archives/ "$DEST/archives/" || fail "rsync of archives/ to $DEST failed"
    echo "$(date '+%Y-%m-%d %H:%M:%S') OK: Backup to $DEST complete"
done

echo "$(date '+%Y-%m-%d %H:%M:%S') OK: USB backup complete"
