#!/bin/bash
# One-command manual copy of .env to a dedicated, offline USB stick.
#
# Deliberately never automated, never scheduled, and never wired into
# backup_offsite_gdrive.sh/backup_usb.sh -- those exist specifically so
# .env doesn't need to be. .env holds every secret this project depends
# on, and deploy.yml runs directly on the production box via a self-hosted
# runner -- so a GitHub Actions compromise already has direct filesystem
# access to the live .env, meaning encrypting a backup copy with a
# GitHub-held passphrase would protect against approximately nothing from
# that direction. A USB stick that's never plugged into anything but this
# one machine, copied to by hand, sidesteps the problem instead of trying
# to patch around it: there's no network path to it at all, so it doesn't
# matter whether GitHub, Google Drive, or the box itself is ever
# compromised.
#
# This only helps if you actually run it again every time .env changes --
# there's no automation reminding you. Worth being a habit, not a script.
#
# Usage: tools/backup_env_to_usb.sh /path/to/mounted/usb/stick
set -e
cd "$(dirname "$0")"

if [ -z "$1" ]; then
    echo "Usage: $0 /path/to/mounted/usb/stick"
    exit 1
fi
DEST="$1"

if [ ! -d "$DEST" ]; then
    echo "❌ $DEST doesn't exist or isn't mounted."
    exit 1
fi

if [ ! -f ../.env ]; then
    echo "❌ ../.env not found -- run this from tools/, or check the file actually exists."
    exit 1
fi

# Two copies, not one:
#   env_latest       always overwritten -- the one file to grab during an
#                     actual rebuild-from-scratch, no guessing which
#                     timestamp is current.
#   env_<timestamp>   a dated copy, kept forever -- not rotated/pruned,
#                     since this is a handful of small text files over the
#                     life of the project, not worth automating cleanup
#                     for. A safety net against ever overwriting
#                     env_latest with a bad or half-edited .env by mistake.
TIMESTAMP=$(date -u '+%Y%m%d_%H%M%S')
cp ../.env "$DEST/env_$TIMESTAMP"
cp ../.env "$DEST/env_latest"

echo "✅ Copied .env to $DEST/env_latest (and $DEST/env_$TIMESTAMP)"
echo "   Remember: this only helps if you run it again next time .env changes."
