#!/bin/bash
# One-off migration: widens workunit.id / result.id (and their known
# cross-reference columns) from 32-bit `int` to 64-bit `bigint unsigned`.
#
# Why: stock BOINC's workunit.id/result.id are signed 32-bit AUTO_INCREMENT
# (~2.147 billion ceiling), never freed even after db_purge deletes old rows.
# Camicia's index space (MAX_INDEX_STR = 653534134886878244999) at the
# default --range_size (1,000,000,000) needs ~6.5e11 workunits to cover --
# ~300x the INT32 ceiling. This is a multi-year-horizon concern, not a today
# emergency, but pre-launch (no real volunteer data yet) is the only time
# this is a cheap, low-risk change -- doing it once real workunit/result
# rows exist would mean a live-traffic schema migration instead.
#
# NOT run automatically by tools.sh (same convention as create_forums.php):
# deploy by hand, then run by hand, once. See tools/migrations/README.md for
# the full migrations convention/log.
#   docker cp ./0001_widen_workunit_result_ids.sh boinc_server:/home/boincadm/projects/camicia/bin/
#   docker exec boinc_server chown boincadm:boincadm /home/boincadm/projects/camicia/bin/0001_widen_workunit_result_ids.sh
#   docker exec boinc_server chmod +x /home/boincadm/projects/camicia/bin/0001_widen_workunit_result_ids.sh
#
# Before running: take a fresh backup (bin/db_backup.sh) and stop the
# daemons (bin/stop) so nothing races the ALTER TABLEs; bin/start after.
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

# Credentials go in a restricted-permission defaults file rather than -p on
# the command line, same reasoning as db_backup.sh.
CREDS_FILE=$(mktemp)
trap 'rm -f "$CREDS_FILE"' EXIT
chmod 600 "$CREDS_FILE"
printf '[client]\nuser=%s\npassword=%s\n' "$DB_USER" "$DB_PASSWD" > "$CREDS_FILE"
MYSQL="mysql --defaults-extra-file=$CREDS_FILE -h $DB_HOST -N -s $DB_NAME"

get_col_field() {
    # $1=table $2=column $3=information_schema.columns field name
    $MYSQL -e "SELECT $3 FROM information_schema.columns WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_NAME='$1' AND COLUMN_NAME='$2'"
}

# The PK/FK pairs known to reference workunit.id/result.id: the core pair
# (workunit.id <-> result.workunitid, result.id <-> workunit.canonical_resultid)
# plus assignment.workunitid/assignment.resultid (BOINC's targeted-work-
# assignment table), confirmed present via the discovery scan below and
# added here after review -- not assumed up front.
KNOWN_COLS=(
    "workunit id"
    "result id"
    "result workunitid"
    "workunit canonical_resultid"
    "assignment workunitid"
    "assignment resultid"
)

echo "== Scanning for other int columns that look like workunit/result ID references =="
CANDIDATES=$($MYSQL -e "
    SELECT CONCAT(TABLE_NAME, '.', COLUMN_NAME)
    FROM information_schema.columns
    WHERE TABLE_SCHEMA = '$DB_NAME'
      AND DATA_TYPE = 'int'
      AND (COLUMN_NAME IN ('workunitid', 'canonical_resultid')
           OR COLUMN_NAME LIKE '%workunit_id%'
           OR COLUMN_NAME LIKE '%result_id%'
           OR (COLUMN_NAME LIKE '%resultid%' AND COLUMN_NAME != 'id'))
")
OTHERS=$(printf '%s\n' "$CANDIDATES" | grep -vE '^(workunit\.id|result\.id|result\.workunitid|workunit\.canonical_resultid|assignment\.workunitid|assignment\.resultid)$' | grep -v '^$' || true)
if [ -n "$OTHERS" ]; then
    echo "⚠️  Found columns beyond the known pair that may ALSO reference workunit/result IDs."
    echo "    NOT altered automatically -- review and widen by hand if they do:"
    echo "$OTHERS" | sed 's/^/    /'
else
    echo "  (none found beyond the known workunit.id / result.id pair)"
fi

echo
echo "== Columns to widen to BIGINT UNSIGNED =="
for pair in "${KNOWN_COLS[@]}"; do
    set -- $pair
    echo "  $1.$2"
done

if [ "$AUTO_YES" != "--yes" ]; then
    echo
    read -p "Proceed with ALTER TABLE on $DB_NAME? [y/N] " CONFIRM
    [ "$CONFIRM" = "y" ] || { echo "Aborted, no changes made."; exit 1; }
fi

widen_column() {
    local table=$1 column=$2
    local current_type
    current_type=$(get_col_field "$table" "$column" "DATA_TYPE")
    if [ "$current_type" != "int" ]; then
        echo "  -> $table.$column is already '$current_type' (not 'int') -- skipping, already migrated or unexpected."
        return
    fi

    local nullable default_val extra clause
    nullable=$(get_col_field "$table" "$column" "IS_NULLABLE")
    # COALESCE'd: a bare NULL COLUMN_DEFAULT (meaning "no default") prints as
    # the literal text "NULL" via the mysql CLI, indistinguishable from an
    # actual default value of NULL -- use a sentinel so "no default" and "the
    # column IS_NULLABLE and no explicit default is set" don't get confused.
    default_val=$($MYSQL -e "SELECT COALESCE(COLUMN_DEFAULT, '__NODEFAULT__') FROM information_schema.columns WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_NAME='$table' AND COLUMN_NAME='$column'")
    extra=$(get_col_field "$table" "$column" "EXTRA")

    clause="BIGINT UNSIGNED"
    [ "$nullable" = "NO" ] && clause="$clause NOT NULL"
    if [ -n "$extra" ]; then
        clause="$clause $extra"
    elif [ "$default_val" != "__NODEFAULT__" ]; then
        clause="$clause DEFAULT $default_val"
    fi

    echo "  -> ALTER TABLE $table MODIFY $column $clause;"
    $MYSQL -e "ALTER TABLE $table MODIFY $column $clause;"
}

echo
echo "== Altering =="
for pair in "${KNOWN_COLS[@]}"; do
    set -- $pair
    widen_column "$1" "$2"
done

echo
echo "== Verifying =="
for pair in "${KNOWN_COLS[@]}"; do
    set -- $pair
    new_type=$($MYSQL -e "SELECT COLUMN_TYPE FROM information_schema.columns WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_NAME='$1' AND COLUMN_NAME='$2'")
    echo "  $1.$2 -> $new_type"
done

echo
echo "Done."
