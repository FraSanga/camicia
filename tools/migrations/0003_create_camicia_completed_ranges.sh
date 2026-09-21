#!/bin/bash
# One-off migration: creates the `camicia_completed_ranges` table in MariaDB.
#
# Why: Maintains a permanent, lightweight ledger of confirmed completed
# permutation blocks (Proposal 4). Provides instant sub-millisecond
# continuity verification, public deal checking, and volunteer statistics
# independent of 7-day BOINC db_purge.
#
# Schema:
#   range_start       DECIMAL(22,0) PRIMARY KEY (10 bytes clustered key)
#   range_end         DECIMAL(22,0)
#   best_deal_index   DECIMAL(22,0)
#   max_cards         SMALLINT UNSIGNED
#   max_tricks        SMALLINT UNSIGNED
#   loops_count       SMALLINT UNSIGNED
#   user_id           INT UNSIGNED
#   verifier_user_id  INT UNSIGNED NULL
#   assimilated_at    INT UNSIGNED
#
# NOT run automatically by tools.sh (same convention as 0001, 0002):
# deploy by hand, then run by hand, once. See tools/migrations/README.md for
# the full migrations convention/log.
#   docker cp ./0003_create_camicia_completed_ranges.sh boinc_server:/home/boincadm/projects/camicia/bin/
#   docker exec boinc_server chown boincadm:boincadm /home/boincadm/projects/camicia/bin/0003_create_camicia_completed_ranges.sh
#   docker exec boinc_server chmod +x /home/boincadm/projects/camicia/bin/0003_create_camicia_completed_ranges.sh
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
TABLE_EXISTS=$($MYSQL -e "SELECT count(*) FROM information_schema.tables WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_NAME='camicia_completed_ranges'")

if [ "$TABLE_EXISTS" -gt 0 ]; then
    echo "  camicia_completed_ranges table already exists."
else
    echo "  camicia_completed_ranges table does not exist and will be created."
fi

if [ "$AUTO_YES" != "--yes" ]; then
    echo
    read -p "Proceed with creating camicia_completed_ranges on $DB_NAME? [y/N] " CONFIRM
    [ "$CONFIRM" = "y" ] || { echo "Aborted, no changes made."; exit 1; }
fi

echo "== Creating camicia_completed_ranges table =="
$MYSQL -e "
CREATE TABLE IF NOT EXISTS camicia_completed_ranges (
    range_start       DECIMAL(22,0)     NOT NULL,
    range_end         DECIMAL(22,0)     NOT NULL,
    best_deal_index   DECIMAL(22,0)     NOT NULL,
    max_cards         SMALLINT UNSIGNED NOT NULL,
    max_tricks        SMALLINT UNSIGNED NOT NULL,
    loops_count       SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    user_id           INT UNSIGNED      NOT NULL,
    verifier_user_id  INT UNSIGNED      NULL DEFAULT NULL,
    assimilated_at    INT UNSIGNED      NOT NULL,
    PRIMARY KEY (range_start),
    INDEX idx_user (user_id),
    INDEX idx_verifier (verifier_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=ascii;
"
echo "  Table created or verified successfully."

# Optional backfill from existing results.txt if present
RESULTS_FILE="./results/results.txt"

backfill_range_info_from_wu() {
    local wu=$1
    $MYSQL -e "
        SELECT COALESCE(w.canonical_resultid, 0), COALESCE(w.mod_time, UNIX_TIMESTAMP())
        FROM workunit w
        WHERE w.name = '$wu' LIMIT 1
    " 2>/dev/null || echo "0 $(date +%s)"
}

backfill_results_for_wu() {
    local wu=$1
    $MYSQL -e "
        SELECT r.id, r.userid, r.outcome
        FROM workunit w
        JOIN result r ON r.workunitid = w.id
        WHERE w.name = '$wu'
    " 2>/dev/null || true
}

parse_range_from_name() {
    local name=$1
    # Strip app prefix "simulator_"
    [[ "$name" =~ ^[a-zA-Z0-9]+_([0-9]+)_([0-9]+) ]] || return 1
    echo "${BASH_REMATCH[1]} ${BASH_REMATCH[2]}"
}

if [ -f "$RESULTS_FILE" ]; then
    echo "== Checking $RESULTS_FILE for completed range backfill =="
    
    # Process results.txt to identify unique workunits and aggregate their stats
    # results.txt lines: wu_name,status,deal_index,cards,tricks
    declare -A WU_BEST_DEAL
    declare -A WU_BEST_CARDS
    declare -A WU_BEST_TRICKS
    declare -A WU_LOOPS

    while IFS=',' read -r wu_n status d_idx cards tricks; do
        [ -n "$wu_n" ] && [ -n "$status" ] || continue
        
        if [ "$status" = "finished" ]; then
            prev_cards=${WU_BEST_CARDS["$wu_n"]:-0}
            if [ "$cards" -ge "$prev_cards" ] 2>/dev/null; then
                WU_BEST_DEAL["$wu_n"]="$d_idx"
                WU_BEST_CARDS["$wu_n"]="$cards"
                WU_BEST_TRICKS["$wu_n"]="$tricks"
            fi
        elif [ "$status" = "loop" ]; then
            prev_loops=${WU_LOOPS["$wu_n"]:-0}
            WU_LOOPS["$wu_n"]=$((prev_loops + 1))
        fi
    done < "$RESULTS_FILE"

    for wu_n in "${!WU_BEST_DEAL[@]}"; do
        read -r r_start r_end <<< "$(parse_range_from_name "$wu_n")" || continue
        [ -n "$r_start" ] && [ -n "$r_end" ] || continue

        EXISTS=$($MYSQL -e "SELECT count(*) FROM camicia_completed_ranges WHERE range_start='$r_start'")
        if [ "$EXISTS" -gt 0 ]; then
            continue
        fi

        b_deal="${WU_BEST_DEAL["$wu_n"]}"
        b_cards="${WU_BEST_CARDS["$wu_n"]:-0}"
        b_tricks="${WU_BEST_TRICKS["$wu_n"]:-0}"
        loops="${WU_LOOPS["$wu_n"]:-0}"

        read -r can_id mod_time <<< "$(backfill_range_info_from_wu "$wu_n")"
        can_id=${can_id:-0}
        mod_time=${mod_time:-$(date +%s)}

        user_id=0
        verifier_user_id="NULL"

        if [ "$can_id" -ne 0 ]; then
            while read -r res_id res_uid res_outcome; do
                [ -n "$res_id" ] || continue
                if [ "$res_id" -eq "$can_id" ]; then
                    user_id=$res_uid
                elif [ "$res_outcome" -eq 1 ] && [ "$verifier_user_id" = "NULL" ]; then
                    verifier_user_id=$res_uid
                fi
            done <<< "$(backfill_results_for_wu "$wu_n")"
        fi

        $MYSQL -e "
            INSERT INTO camicia_completed_ranges
            (range_start, range_end, best_deal_index, max_cards, max_tricks, loops_count, user_id, verifier_user_id, assimilated_at)
            VALUES
            ('$r_start', '$r_end', '$b_deal', $b_cards, $b_tricks, $loops, $user_id, $verifier_user_id, $mod_time)
        "
        echo "  Backfilled range: $r_start - $r_end (user $user_id, verifier $verifier_user_id)"
    done
fi

echo
echo "== Migration 0003 completed successfully =="
