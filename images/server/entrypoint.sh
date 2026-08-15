#!/bin/bash
set -e

PROJECT_DIR=${SERVER_VOLUME_PROJECTS_DIR}/camicia
export PYTHONPATH=$PYTHONPATH:/usr/local/src/boinc/py

echo "🚀 [BOOT] Starting BOINC Server..."

echo "⏳ [DB-CHECK] Waiting database to start (boinc_db)..."
while ! bash -c 'echo > /dev/tcp/boinc_db/3306' 2>/dev/null; do
    echo "   -> Database not ready. Retry in 3 seconds..."
    sleep 3
done

if [ -d "$PROJECT_DIR" ]; then
    MISSING=""
    for f in config.xml bin/start bin/stop html/ops keys/code_sign_private; do
        [ -e "$PROJECT_DIR/$f" ] || MISSING="$MISSING $f"
    done
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
sed -i "s|\${SERVER_VOLUME_PROJECTS_DIR}|${SERVER_VOLUME_PROJECTS_DIR}|g" /etc/apache2/sites-available/boinc.conf
sed -i "s|\${DOMAIN}|${DOMAIN}|g" /etc/apache2/sites-available/boinc.conf

echo "🌐 [HTTPD] Starting Apache..."
rm -f /var/run/apache2/apache2.pid
exec apachectl -D FOREGROUND