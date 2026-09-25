#!/bin/bash
# One-off migration: creates the `camicia_histogram_totals` table in MariaDB
# and adds the `histogram_applied` idempotency flag to `camicia_completed_ranges`.
#
# Why: Maintains a permanent macro-distribution statistical ledger of Beggar-My-Neighbour
# game outcomes. Arbitrary-precision DECIMAL types guarantee future-safe
# scaling across all 6.535e20 deals without integer overflow. The histogram_applied
# flag on camicia_completed_ranges ensures strict idempotency and zero double-counting
# across server restarts or power failures.
#
# Schema:
#   id              TINYINT UNSIGNED PRIMARY KEY DEFAULT 1
#   total_deals     DECIMAL(24,0) NOT NULL DEFAULT 0
#   total_cards     DECIMAL(28,0) NOT NULL DEFAULT 0
#   total_tricks    DECIMAL(28,0) NOT NULL DEFAULT 0
#   total_cards_sq  DECIMAL(34,0) NOT NULL DEFAULT 0
#   p1_wins         DECIMAL(24,0) NOT NULL DEFAULT 0
#   bucket_0..63    DECIMAL(24,0) NOT NULL DEFAULT 0
#
# Deploy instructions:
#   docker cp ./0004_create_camicia_histogram_totals.sh boinc_server:/home/boincadm/projects/camicia/bin/
#   docker exec boinc_server chown boincadm:boincadm /home/boincadm/projects/camicia/bin/0004_create_camicia_histogram_totals.sh
#   docker exec boinc_server chmod +x /home/boincadm/projects/camicia/bin/0004_create_camicia_histogram_totals.sh
#
# Safe to re-run (CREATE TABLE IF NOT EXISTS, ADD COLUMN IF NOT EXISTS).
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
TABLE_EXISTS=$($MYSQL -e "SELECT count(*) FROM information_schema.tables WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_NAME='camicia_histogram_totals'")

if [ "$TABLE_EXISTS" -gt 0 ]; then
    echo "  camicia_histogram_totals table already exists."
else
    echo "  camicia_histogram_totals table does not exist and will be created."
fi

if [ "$AUTO_YES" != "--yes" ]; then
    echo
    read -p "Proceed with creating camicia_histogram_totals on $DB_NAME? [y/N] " CONFIRM
    [ "$CONFIRM" = "y" ] || { echo "Aborted, no changes made."; exit 1; }
fi

# Build bucket column definitions
BUCKET_COLS=""
for i in $(seq 0 63); do
    if [ "$i" -eq 63 ]; then
        BUCKET_COLS="${BUCKET_COLS}    bucket_${i} DECIMAL(24,0) NOT NULL DEFAULT 0"
    else
        BUCKET_COLS="${BUCKET_COLS}    bucket_${i} DECIMAL(24,0) NOT NULL DEFAULT 0,
"
    fi
done

echo "== Creating camicia_histogram_totals table =="
$MYSQL -e "
CREATE TABLE IF NOT EXISTS camicia_histogram_totals (
    id             TINYINT UNSIGNED PRIMARY KEY DEFAULT 1,
    total_deals    DECIMAL(24,0) NOT NULL DEFAULT 0,
    total_cards    DECIMAL(28,0) NOT NULL DEFAULT 0,
    total_tricks   DECIMAL(28,0) NOT NULL DEFAULT 0,
    total_cards_sq DECIMAL(34,0) NOT NULL DEFAULT 0,
    p1_wins        DECIMAL(24,0) NOT NULL DEFAULT 0,
${BUCKET_COLS}
) ENGINE=InnoDB DEFAULT CHARSET=ascii;
"
echo "  Table created or verified successfully."

echo "== Ensuring single aggregate row id=1 exists =="
$MYSQL -e "INSERT IGNORE INTO camicia_histogram_totals (id) VALUES (1);"

echo "== Adding histogram_applied flag to camicia_completed_ranges =="
$MYSQL -e "
ALTER TABLE camicia_completed_ranges
ADD COLUMN IF NOT EXISTS histogram_applied TINYINT(1) NOT NULL DEFAULT 0;
"
echo "  Column histogram_applied verified on camicia_completed_ranges."

# Optional backfill from existing results.txt if any summary lines are present
RESULTS_FILE="./results/results.txt"
if [ -f "$RESULTS_FILE" ]; then
    SUMMARY_COUNT=$(grep -c ",summary," "$RESULTS_FILE" || true)
    if [ "$SUMMARY_COUNT" -gt 0 ]; then
        echo "== Backfilling $SUMMARY_COUNT summary record(s) from $RESULTS_FILE =="
        while IFS= read -r line; do
            case "$line" in
                *,summary,*)
                    # Format: <wu_name>,summary,<deals>,<total_cards>,<total_tricks>,<total_cards_sq>,<p1_wins>,<b0>...
                    WU_NAME=$(echo "$line" | cut -d, -f1)
                    START=$(echo "$WU_NAME" | cut -d_ -f2)
                    
                    APPLIED=$($MYSQL -e "SELECT COALESCE(histogram_applied, 0) FROM camicia_completed_ranges WHERE range_start = '$START'" 2>/dev/null || echo "0")
                    if [ "$APPLIED" = "1" ]; then
                        continue
                    fi

                    DEALS=$(echo "$line" | cut -d, -f3)
                    CARDS=$(echo "$line" | cut -d, -f4)
                    TRICKS=$(echo "$line" | cut -d, -f5)
                    CARDS_SQ=$(echo "$line" | cut -d, -f6)
                    P1_WINS=$(echo "$line" | cut -d, -f7)

                    UPDATE_SQL="UPDATE camicia_histogram_totals SET total_deals = total_deals + $DEALS, total_cards = total_cards + $CARDS, total_tricks = total_tricks + $TRICKS, total_cards_sq = total_cards_sq + $CARDS_SQ, p1_wins = p1_wins + $P1_WINS"
                    for b in $(seq 0 63); do
                        FIELD_IDX=$((b + 8))
                        B_VAL=$(echo "$line" | cut -d, -f$FIELD_IDX)
                        UPDATE_SQL="${UPDATE_SQL}, bucket_${b} = bucket_${b} + $B_VAL"
                    done
                    UPDATE_SQL="${UPDATE_SQL} WHERE id = 1;"

                    $MYSQL -e "
                    START TRANSACTION;
                    $UPDATE_SQL
                    UPDATE camicia_completed_ranges SET histogram_applied = 1 WHERE range_start = '$START';
                    COMMIT;
                    "
                    ;;
            esac
        done < "$RESULTS_FILE"
        echo "  Backfill complete."
    else
        echo "  No summary lines found in $RESULTS_FILE (clean slate)."
    fi
fi

echo "Migration 0004 complete."
