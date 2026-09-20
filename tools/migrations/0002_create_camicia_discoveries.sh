#!/bin/bash
# One-off migration: creates the `camicia_discoveries` table in MariaDB.
#
# Why: stock BOINC's db_purge permanently deletes workunit and result rows
# after ~7 days (--min_age_days 7). badge_assign_discovery.php originally
# relied on traversing workunit.canonical_resultid -> result.hostid ->
# host.userid to attribute discoveries, which fails once those rows are purged.
# camicia_discoveries persists every loop discovery and record-breaking game
# (and all historical record milestones from Day 1) with direct volunteer
# attribution (userid, hostid), immune to db_purge.
#
# NOT run automatically by tools.sh (same convention as 0001 and create_forums.php):
# deploy by hand, then run by hand, once. See tools/migrations/README.md for
# the full migrations convention/log.
#   docker cp ./0002_create_camicia_discoveries.sh boinc_server:/home/boincadm/projects/camicia/bin/
#   docker exec boinc_server chown boincadm:boincadm /home/boincadm/projects/camicia/bin/0002_create_camicia_discoveries.sh
#   docker exec boinc_server chmod +x /home/boincadm/projects/camicia/bin/0002_create_camicia_discoveries.sh
#
# Safe to re-run (CREATE TABLE IF NOT EXISTS, skips existing records on backfill).
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
TABLE_EXISTS=$($MYSQL -e "SELECT count(*) FROM information_schema.tables WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_NAME='camicia_discoveries'")

if [ "$TABLE_EXISTS" -gt 0 ]; then
    echo "  camicia_discoveries table already exists."
else
    echo "  camicia_discoveries table does not exist and will be created."
fi

if [ "$AUTO_YES" != "--yes" ]; then
    echo
    read -p "Proceed with creating camicia_discoveries on $DB_NAME? [y/N] " CONFIRM
    [ "$CONFIRM" = "y" ] || { echo "Aborted, no changes made."; exit 1; }
fi

echo "== Creating camicia_discoveries table =="
$MYSQL -e "
CREATE TABLE IF NOT EXISTS camicia_discoveries (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    discovery_type ENUM('loop', 'longest') NOT NULL,
    deal_index VARCHAR(64) NOT NULL,
    cards BIGINT NOT NULL,
    tricks BIGINT NOT NULL,
    wu_name VARCHAR(128) NOT NULL,
    userid BIGINT NOT NULL,
    hostid BIGINT NOT NULL,
    discovered_at INT UNSIGNED NOT NULL,
    is_world_record TINYINT(1) NOT NULL DEFAULT 0,
    INDEX idx_user (userid),
    INDEX idx_type_cards (discovery_type, cards DESC),
    INDEX idx_discovered (discovered_at),
    INDEX idx_deal (deal_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"
echo "  Table created or verified successfully."

# Optional backfill from existing flat files if any already exist on disk
REAL_WORLD_RECORD_CARDS=8344
LOOPS_FILE="./records_loops.txt"
LONGEST_FILE="./records_longest.txt"

backfill_user_from_wu() {
    local wu=$1
    $MYSQL -e "
        SELECT COALESCE(h.userid, 0), COALESCE(r.hostid, 0)
        FROM workunit w
        JOIN result r ON w.canonical_resultid = r.id
        JOIN host h ON r.hostid = h.id
        WHERE w.name = '$wu' LIMIT 1
    " 2>/dev/null || echo "0 0"
}

if [ -f "$LOOPS_FILE" ]; then
    echo "== Checking records_loops.txt for backfill =="
    while read -r line; do
        [ -n "$line" ] || continue
        read -r d_idx wu_n ts u_id h_id <<< "$line"
        [ -n "$d_idx" ] && [ -n "$wu_n" ] || continue
        ts=${ts:-$(date +%s)}
        
        # If user/host not in file, attempt DB lookup
        if [ -z "$u_id" ] || [ "$u_id" -eq 0 ] 2>/dev/null; then
            read -r db_uid db_hid <<< "$(backfill_user_from_wu "$wu_n")"
            u_id=${db_uid:-0}
            h_id=${db_hid:-0}
        fi

        EXISTS=$($MYSQL -e "SELECT count(*) FROM camicia_discoveries WHERE discovery_type='loop' AND deal_index='$d_idx'")
        if [ "$EXISTS" -eq 0 ]; then
            $MYSQL -e "INSERT INTO camicia_discoveries (discovery_type, deal_index, cards, tricks, wu_name, userid, hostid, discovered_at, is_world_record) VALUES ('loop', '$d_idx', 0, 0, '$wu_n', $u_id, $h_id, $ts, 0)"
            echo "  Backfilled loop: deal $d_idx ($wu_n) -> userid $u_id"
        fi
    done < "$LOOPS_FILE"
fi

if [ -f "$LONGEST_FILE" ]; then
    echo "== Checking records_longest.txt for backfill =="
    line=$(tr -s ' ' < "$LONGEST_FILE" | head -n 1)
    if [ -n "$line" ]; then
        read -r cards tricks d_idx wu_n ts u_id h_id <<< "$line"
        if [ -n "$cards" ] && [ -n "$d_idx" ] && [ -n "$wu_n" ]; then
            ts=${ts:-$(date +%s)}
            if [ -z "$u_id" ] || [ "$u_id" -eq 0 ] 2>/dev/null; then
                read -r db_uid db_hid <<< "$(backfill_user_from_wu "$wu_n")"
                u_id=${db_uid:-0}
                h_id=${db_hid:-0}
            fi
            is_wr=0
            if [ "$cards" -gt "$REAL_WORLD_RECORD_CARDS" ] 2>/dev/null; then
                is_wr=1
            fi
            EXISTS=$($MYSQL -e "SELECT count(*) FROM camicia_discoveries WHERE discovery_type='longest' AND deal_index='$d_idx'")
            if [ "$EXISTS" -eq 0 ]; then
                $MYSQL -e "INSERT INTO camicia_discoveries (discovery_type, deal_index, cards, tricks, wu_name, userid, hostid, discovered_at, is_world_record) VALUES ('longest', '$d_idx', $cards, $tricks, '$wu_n', $u_id, $h_id, $ts, $is_wr)"
                echo "  Backfilled longest: deal $d_idx ($cards cards) -> userid $u_id"
            fi
        fi
    fi
fi

echo
echo "== Migration 0002 completed successfully =="
