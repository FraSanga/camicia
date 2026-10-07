#!/bin/bash
# One-off migration: creates `camicia_duel_stats` and `camicia_duel_matches` tables in MariaDB.
#
# Why: Supports the multiplayer `/duel` feature on CamiciaBot.
# Tracks Elo ratings, win/loss/tie records, daily casual and ranked duel counts,
# user preferences (direct challenge opt-out), and a complete match history ledger.
#
# NOT run automatically by tools.sh (same convention as 0001-0005):
# deploy by hand, then run by hand, once. See tools/migrations/README.md for
# the full migrations convention/log.
#   docker cp ./0006_create_camicia_duels.sh boinc_server:/home/boincadm/projects/camicia/bin/
#   docker exec boinc_server chown boincadm:boincadm /home/boincadm/projects/camicia/bin/0006_create_camicia_duels.sh
#   docker exec boinc_server chmod +x /home/boincadm/projects/camicia/bin/0006_create_camicia_duels.sh
#   docker exec boinc_server bin/0006_create_camicia_duels.sh --yes
#
# Safe to re-run (CREATE TABLE IF NOT EXISTS).
set -e
AUTO_YES="${1:-}"
cd "$(dirname "$0")/.."

CONFIG="./config.xml"
if [ ! -f "$CONFIG" ] && [ -f "../config.xml" ]; then
    CONFIG="../config.xml"
fi

db_field() {
    [ -f "$CONFIG" ] && grep -oP "(?<=<$1>).*(?=</$1>)" "$CONFIG" | head -1 || true
}

DB_HOST=$(db_field db_host)
DB_USER=$(db_field db_user)
DB_PASSWD=$(db_field db_passwd)
DB_NAME=$(db_field db_name)

# If config.xml was not present, look for .env in project root or current dir
if [ -z "$DB_PASSWD" ]; then
    ENV_FILE=""
    if [ -f "./.env" ]; then
        ENV_FILE="./.env"
    elif [ -f "../.env" ]; then
        ENV_FILE="../.env"
    fi
    if [ -n "$ENV_FILE" ]; then
        get_env_val() {
            grep -oP "(?<=^$1=).*" "$ENV_FILE" | tr -d '"' | tr -d "'" | head -1 || true
        }
        [ -z "$DB_HOST" ] && DB_HOST=$(get_env_val MARIADB_HOST)
        [ -z "$DB_USER" ] && DB_USER=$(get_env_val MARIADB_USER)
        [ -z "$DB_PASSWD" ] && DB_PASSWD=$(get_env_val MARIADB_PASSWORD)
        [ -z "$DB_PASSWD" ] && DB_PASSWD=$(get_env_val MARIADB_ROOT_PASSWORD)
        [ -z "$DB_NAME" ] && DB_NAME=$(get_env_val MARIADB_DATABASE)
        [ -z "$DB_PORT" ] && DB_PORT=$(get_env_val MARIADB_PORT)
    fi
fi

DB_HOST="${DB_HOST:-${MARIADB_HOST:-127.0.0.1}}"
DB_USER="${DB_USER:-${MARIADB_USER:-root}}"
DB_PASSWD="${DB_PASSWD:-${MARIADB_PASSWORD:-${MARIADB_ROOT_PASSWORD:-}}}"
DB_NAME="${DB_NAME:-${MARIADB_DATABASE:-camicia}}"
DB_PORT="${DB_PORT:-${MARIADB_PORT:-3306}}"

CREDS_FILE=$(mktemp)
trap 'rm -f "$CREDS_FILE"' EXIT
chmod 600 "$CREDS_FILE"
printf '[client]\nuser=%s\npassword=%s\n' "$DB_USER" "$DB_PASSWD" > "$CREDS_FILE"
MYSQL="mysql --defaults-extra-file=$CREDS_FILE -h $DB_HOST -P $DB_PORT -N -s $DB_NAME"

echo "== Checking table status in $DB_NAME =="
STATS_EXISTS=$($MYSQL -e "SELECT count(*) FROM information_schema.tables WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_NAME='camicia_duel_stats'")
MATCHES_EXISTS=$($MYSQL -e "SELECT count(*) FROM information_schema.tables WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_NAME='camicia_duel_matches'")

if [ "$STATS_EXISTS" -gt 0 ]; then
    echo "  camicia_duel_stats table already exists."
else
    echo "  camicia_duel_stats table does not exist and will be created."
fi

if [ "$MATCHES_EXISTS" -gt 0 ]; then
    echo "  camicia_duel_matches table already exists."
else
    echo "  camicia_duel_matches table does not exist and will be created."
fi

if [ "$AUTO_YES" != "--yes" ]; then
    echo
    read -p "Proceed with creating duel tables on $DB_NAME? [y/N] " CONFIRM
    [ "$CONFIRM" = "y" ] || { echo "Aborted, no changes made."; exit 1; }
fi

echo "== Creating camicia_duel_stats table =="
$MYSQL -e "
CREATE TABLE IF NOT EXISTS camicia_duel_stats (
    discord_id BIGINT UNSIGNED PRIMARY KEY,
    boinc_user_id INT UNSIGNED NULL,
    elo_rating INT NOT NULL DEFAULT 1000,
    wins INT UNSIGNED NOT NULL DEFAULT 0,
    losses INT UNSIGNED NOT NULL DEFAULT 0,
    ties INT UNSIGNED NOT NULL DEFAULT 0,
    daily_casual_count INT UNSIGNED NOT NULL DEFAULT 0,
    daily_ranked_count INT UNSIGNED NOT NULL DEFAULT 0,
    last_played_date DATE NULL,
    direct_challenges_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_elo (elo_rating DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"

echo "== Creating camicia_duel_matches table =="
$MYSQL -e "
CREATE TABLE IF NOT EXISTS camicia_duel_matches (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    mode ENUM('casual', 'ranked') NOT NULL,
    player1_id BIGINT UNSIGNED NOT NULL,
    player2_id BIGINT UNSIGNED NOT NULL,
    winner_id BIGINT UNSIGNED NULL,
    cards_played INT UNSIGNED NOT NULL,
    tricks INT UNSIGNED NOT NULL,
    series_score VARCHAR(16) NULL,
    status ENUM('completed', 'finished', 'loop', 'record', 'cancelled') NOT NULL,
    played_date DATE NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_p1_p2_date (player1_id, player2_id, played_date),
    INDEX idx_p2_p1_date (player2_id, player1_id, played_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"

echo "== Verifying table creation =="
COL_STATS=$($MYSQL -e "SELECT count(*) FROM information_schema.columns WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_NAME='camicia_duel_stats'")
COL_MATCHES=$($MYSQL -e "SELECT count(*) FROM information_schema.columns WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_NAME='camicia_duel_matches'")
echo "  Table camicia_duel_stats verified ($COL_STATS columns present)."
echo "  Table camicia_duel_matches verified ($COL_MATCHES columns present)."

echo "== Migration completed successfully =="
