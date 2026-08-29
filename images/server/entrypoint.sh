#!/bin/bash
set -e

PROJECT_DIR=${SERVER_VOLUME_PROJECTS_DIR}/camicia
KEY_DIR=${SERVER_VOLUME_KEYS_DIR}
export PYTHONPATH=$PYTHONPATH:/usr/local/src/boinc/py

echo "🚀 [BOOT] Starting BOINC Server..."

# "database", not $DATABASE_CONTAINER_NAME: Docker Compose always resolves
# the service name itself (the docker-compose.yml key, "database") within
# the shared network, regardless of what container_name/.env sets it to --
# stable across every environment, not just whichever one happens to name
# its container "boinc_db".
echo "⏳ [DB-CHECK] Waiting database to start (database)..."
while ! bash -c 'echo > /dev/tcp/database/3306' 2>/dev/null; do
    echo "   -> Database not ready. Retry in 3 seconds..."
    sleep 3
done

if [ -d "$PROJECT_DIR" ]; then
    MISSING=""
    for f in config.xml bin/start bin/stop html/ops; do
        [ -e "$PROJECT_DIR/$f" ] || MISSING="$MISSING $f"
    done
    # code_sign_public specifically, not code_sign_private: keys live on
    # their own persistent mount (KEY_DIR, not PROJECT_DIR) since the
    # persistent-keys migration, and code_sign_private is normally encrypted
    # at rest / only briefly plaintext during a tools.sh deploy -- checking
    # for it here would false-positive "incomplete bootstrap" on every
    # ordinary container restart. code_sign_public is always plaintext
    # (it's meant to be public) and equally good evidence keys exist.
    [ -e "$KEY_DIR/code_sign_public" ] || MISSING="$MISSING $KEY_DIR/code_sign_public"
    if [ -n "$MISSING" ]; then
        echo "❌ [BOOT] ERROR: $PROJECT_DIR exists but bootstrap looks INCOMPLETE."
        echo "   Missing:$MISSING"
        echo "   This usually means a previous 'make_project' run was interrupted."
        echo "   Fix: remove $PROJECT_DIR and re-run make_project (README.md §2), then"
        echo "   re-run tools/tools.sh before restarting this container."
        exit 1
    fi

    /usr/local/bin/fix_permissions.sh

    # make_project only writes camicia.cronjob to disk -- installing it into
    # an actual crontab is a manual step in BOINC's own bootstrap docs
    # ("Add to crontab (as boincadm): crontab -e ..."), easy to miss and
    # silent when missed: cron itself starts fine below, it just never has
    # anything to run, so every <task> in config.xml (db_backup.sh,
    # disk_space_check.sh, memory_check.sh, rotate_results.sh, etc.) silently
    # never fires. Install unconditionally and idempotently (crontab -u
    # overwrites, not appends) so this self-heals on every container start,
    # the same reasoning as fix_permissions.sh above.
    if [ -f "$PROJECT_DIR/camicia.cronjob" ]; then
        crontab -u "${PROJECTS_USER}" "$PROJECT_DIR/camicia.cronjob"
    fi
fi

if [ -f "$PROJECT_DIR/bin/start" ]; then
    echo "⚙️ [BOINC] Starting project daemons as ${PROJECTS_USER}..."
    su -s /bin/bash "${PROJECTS_USER}" -c "export PYTHONPATH=$PYTHONPATH; cd $PROJECT_DIR && ./bin/start"
fi

echo "⏰ [CRON] Starting Cron..."
service cron start

echo "⚙️ [HTTPD] Configuring Apache VirtualHost with secure variables..."
# Regenerated fresh from the pristine template on every start, not sed -i on
# the live file -- otherwise the first substitution consumes the placeholders
# and a later change to DOMAIN/SERVER_VOLUME_PROJECTS_DIR in .env would never
# take effect on a plain container restart, only on a full recreate.
sed -e "s|\${SERVER_VOLUME_PROJECTS_DIR}|${SERVER_VOLUME_PROJECTS_DIR}|g" \
    -e "s|\${DOMAIN}|${DOMAIN}|g" \
    /etc/apache2/sites-available/boinc.conf.template > /etc/apache2/sites-available/boinc.conf

echo "🌐 [HTTPD] Starting Apache..."
rm -f /var/run/apache2/apache2.pid
exec apachectl -D FOREGROUND