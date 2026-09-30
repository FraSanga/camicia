#!/bin/bash
# One-off migration: creates the `camicia_discord_links` table in MariaDB.
#
# Why: Allows BOINC volunteers to connect their Discord account to their
# Camicia project account. Volunteers request a 6-digit verification PIN via
# email on the website (rate-limited to 1 per 60s, expires in 15m), and
# confirm it via /link in Discord to achieve the "Volunteer" role and unlock
# +2 daily /lucky rolls.
#
# NOT run automatically by tools.sh (same convention as 0001-0004):
# deploy by hand, then run by hand, once. See tools/migrations/README.md for
# the full migrations convention/log.
#   docker cp ./0005_create_camicia_discord_links.sh boinc_server:/home/boincadm/projects/camicia/bin/
#   docker exec boinc_server chown boincadm:boincadm /home/boincadm/projects/camicia/bin/0005_create_camicia_discord_links.sh
#   docker exec boinc_server chmod +x /home/boincadm/projects/camicia/bin/0005_create_camicia_discord_links.sh
#   docker exec boinc_server bin/0005_create_camicia_discord_links.sh --yes
#
# Safe to re-run (CREATE TABLE IF NOT EXISTS).
set -e
AUTO_YES="$1"
cd "$(dirname "$0")/.."

CONFIG="./config.xml"
db_field() {
    grep -oP "(?<=<$1>).*(?=</$1>)" "$CONFIG" | head -1
}
DB_HOST=$(db_field db_host)
DB_USER=$(db_field db_user)
DB_PASSWD=$(db_field db_passwd)
DB_NAME=$(db_field db_name)

CREDS_FILE=$(mktemp)
trap 'rm -f "$CREDS_FILE"' EXIT
chmod 600 "$CREDS_FILE"
printf '[client]\nuser=%s\npassword=%s\n' "$DB_USER" "$DB_PASSWD" > "$CREDS_FILE"
MYSQL="mysql --defaults-extra-file=$CREDS_FILE -h $DB_HOST -N -s $DB_NAME"

echo "== Checking table status in $DB_NAME =="
TABLE_EXISTS=$($MYSQL -e "SELECT count(*) FROM information_schema.tables WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_NAME='camicia_discord_links'")

if [ "$TABLE_EXISTS" -gt 0 ]; then
    echo "  camicia_discord_links table already exists."
else
    echo "  camicia_discord_links table does not exist and will be created."
fi

if [ "$AUTO_YES" != "--yes" ]; then
    echo
    read -p "Proceed with creating camicia_discord_links on $DB_NAME? [y/N] " CONFIRM
    [ "$CONFIRM" = "y" ] || { echo "Aborted, no changes made."; exit 1; }
fi

echo "== Creating camicia_discord_links table =="
$MYSQL -e "
CREATE TABLE IF NOT EXISTS camicia_discord_links (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    boinc_user_id INT UNSIGNED NOT NULL,
    discord_id BIGINT UNSIGNED NULL,
    discord_username VARCHAR(128) NULL,
    pin VARCHAR(16) NULL,
    pin_requested_at DATETIME NULL,
    pin_expires_at DATETIME NULL,
    linked_at DATETIME NULL,
    unlinked_at DATETIME NULL,
    UNIQUE KEY uq_boinc_user (boinc_user_id),
    UNIQUE KEY uq_discord_id (discord_id),
    INDEX idx_pin (pin),
    INDEX idx_unlinked (unlinked_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Ensure unlinked_at column and index exist if table was already created
ALTER TABLE camicia_discord_links ADD COLUMN IF NOT EXISTS unlinked_at DATETIME NULL;
SET @exist := (SELECT count(*) FROM information_schema.statistics WHERE table_name='camicia_discord_links' AND index_name='idx_unlinked' AND table_schema='$DB_NAME');
SET @sqlstmt := IF(@exist = 0, 'CREATE INDEX idx_unlinked ON camicia_discord_links (unlinked_at)', 'SELECT 1');
PREPARE stmt FROM @sqlstmt;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
"

echo "== Verifying table creation =="
COL_COUNT=$($MYSQL -e "SELECT count(*) FROM information_schema.columns WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_NAME='camicia_discord_links'")
echo "  Table camicia_discord_links verified ($COL_COUNT columns present)."

echo "== Migration completed successfully =="
