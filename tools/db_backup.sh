#!/bin/bash
# Dumps the project's MariaDB database to db_backups/, gzip'd and timestamped,
# keeping the last $RETAIN copies. Reads DB connection info out of the
# project's own config.xml (populated by make_project) rather than requiring
# separate credentials to be threaded into the container's environment.
set -e
cd "$(dirname "$0")/.."

RETAIN=7
BACKUP_DIR="./db_backups"
CONFIG="./config.xml"

db_field() {
    grep -oP "(?<=<$1>).*(?=</$1>)" "$CONFIG" | head -1
}

DB_HOST=$(db_field db_host)
DB_USER=$(db_field db_user)
DB_PASSWD=$(db_field db_passwd)
DB_NAME=$(db_field db_name)

mkdir -p "$BACKUP_DIR"
STAMP=$(date +%Y%m%d_%H%M%S)
OUT="$BACKUP_DIR/${DB_NAME}_${STAMP}.sql.gz"

# Credentials go in a restricted-permission defaults file rather than -p on the
# command line, which would otherwise be visible to any other process on the
# host via `ps`/`/proc/<pid>/cmdline`.
CREDS_FILE=$(mktemp)
trap 'rm -f "$CREDS_FILE"' EXIT
chmod 600 "$CREDS_FILE"
printf '[client]\nuser=%s\npassword=%s\n' "$DB_USER" "$DB_PASSWD" > "$CREDS_FILE"

mysqldump --defaults-extra-file="$CREDS_FILE" -h "$DB_HOST" "$DB_NAME" | gzip > "$OUT"

# retention: keep the newest $RETAIN dumps
ls -1t "$BACKUP_DIR"/${DB_NAME}_*.sql.gz 2>/dev/null | tail -n +$((RETAIN + 1)) | xargs -r rm -f

echo "Backed up $DB_NAME to $OUT"
