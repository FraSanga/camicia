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