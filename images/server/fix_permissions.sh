#!/bin/bash
# Fixes ownership/permissions across the BOINC project tree. Must run as root
# (usermod, and chown to www-data, both need it).
#
# Called from two places, both required:
#  - entrypoint.sh, at container boot, IF the project already existed at that
#    point (e.g. a restart of an already-bootstrapped container).
#  - tools/tools.sh, on every deploy -- this is the one that actually matters
#    for a fresh project: the documented bootstrap workflow runs make_project
#    *after* the container is already up (README.md SS1-2), so at container-boot
#    time the project doesn't exist yet and entrypoint.sh's own call is a
#    no-op. Without tools.sh also calling this, www-data never gets read
#    access to the just-created html/ tree and the user site 500s forever.
set -e

PROJECT_DIR="${SERVER_VOLUME_PROJECTS_DIR}/camicia"
KEY_DIR="${SERVER_VOLUME_KEYS_DIR}"

echo "🔐 www-data permissions..."
usermod -a -G "${PROJECTS_USER}" www-data
chown -R "${PROJECTS_USER}":"${PROJECTS_USER}" "$PROJECT_DIR"

# camicia.httpd.conf's only Alias for the user-facing site is
# /camicia -> html/user, so anything profile.inc builds URLs for under
# IMAGE_URL/PROFILE_PATH ("user_profile/...", relative to that same
# /camicia prefix) needs html/user/user_profile to actually resolve to
# the real html/user_profile/ directory one level up -- make_project's
# own setup_project.py only ever creates html/user_profile/images/, it
# never wires this into the web-reachable tree (confirmed by reading
# it: no symlink, no Apache Alias, just mkdir). Without this, every
# profile picture and every page update_profile_pages.php generates
# (gallery, alphabetical/country listings) 404s -- silently broken
# since this project's first day, only actually noticed once someone
# uploaded a real profile picture. -sfn: safe to re-run every deploy,
# replaces a stale symlink instead of erroring on one that already
# exists.
ln -sfn ../user_profile "$PROJECT_DIR/html/user/user_profile"

find "$PROJECT_DIR/html" -type d -exec chmod 775 {} +
find "$PROJECT_DIR/html" -type f -exec chmod 664 {} +

# bin/run_in_ops executes ops/*.php scripts directly (`./script.php`, relying
# on their own #!/usr/bin/env php shebang) rather than invoking `php
# script.php` -- so they need the execute bit the blanket chmod 664 above
# just stripped. Found via a full task-log audit: 8 tasks (autolock,
# badge_assign, delete_expired_tokens, delete_expired_users_and_hosts,
# notify, update_forum_activities, update_profile_pages, update_uotd) were
# silently failing with "Permission denied" on every single run since the
# crontab fix made tasks actually execute. create_forums.php (deployed by
# tools.sh, which chmod +x's it explicitly after this script runs) never hit
# this; every *other* ops/*.php script -- generated once by make_project and
# never touched again -- did.
chmod +x "$PROJECT_DIR"/html/ops/*.php

echo "🔐 _ops access configuration..."
OPS_USER_EFFECTIVE=${OPS_USER:-admin}
OPS_PASS_EFFECTIVE=${OPS_PASS:-admin}
htpasswd -b -c "$PROJECT_DIR/html/ops/.htpasswd" "$OPS_USER_EFFECTIVE" "$OPS_PASS_EFFECTIVE"
chown www-data:www-data "$PROJECT_DIR/html/ops/.htpasswd"
chmod 640 "$PROJECT_DIR/html/ops/.htpasswd"

echo "🔐 Locking down code-signing/upload keys..."
# Keys live on their own persistent bind mount (SERVER_VOLUME_KEYS), separate from
# PROJECT_DIR, specifically so they survive a from-scratch wipe-and-rebootstrap of the
# project tree -- see config.xml's <key_dir>, which every BOINC tool reads at runtime.
chown -R "${PROJECTS_USER}":"${PROJECTS_USER}" "$KEY_DIR"
# Private keys: only boincadm ever needs these (tools.sh signs as boincadm) -> owner-only.
chmod 600 "$KEY_DIR"/*_private 2>/dev/null || true
# code_sign_private.gpg: encrypted at rest, but still locked to owner-only as defense in
# depth (doesn't match the *_private glob above since it ends in .gpg, not _private).
chmod 600 "$KEY_DIR"/*.gpg 2>/dev/null || true
# Public keys: the scheduler CGI (runs as www-data, in the boincadm group) reads these on
# every scheduler request to verify app signatures -> must stay group-readable, or every
# client request silently fails with "Server can't find key file" and 0 tasks are ever sent.
chmod 640 "$KEY_DIR"/*_public 2>/dev/null || true

echo "🔐 Locking down config.xml (contains the DB root password)..."
# www-data needs group-read: the ops PHP pages parse config.xml directly.
chown "${PROJECTS_USER}":www-data "$PROJECT_DIR/config.xml" 2>/dev/null || true
chmod 640 "$PROJECT_DIR/config.xml" 2>/dev/null || true

echo "🔧 Setting permissions for upload/download/logs/pid..."
chown -R "${PROJECTS_USER}:www-data" "$PROJECT_DIR/upload"
chown -R "${PROJECTS_USER}:www-data" "$PROJECT_DIR/download"
chown -R "${PROJECTS_USER}:www-data" "$PROJECT_DIR/log_${SERVER_HOSTNAME}"
chmod -R 2770 "${PROJECT_DIR}/upload"
chmod -R 2770 "${PROJECT_DIR}/download"
chmod -R 2770 "${PROJECT_DIR}/log_${SERVER_HOSTNAME}"

echo "✅ Permissions successfully configured"
