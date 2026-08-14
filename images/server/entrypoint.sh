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

    echo "🔐 www-data permissions..."
    usermod -a -G "${PROJECTS_USER}" www-data
    chown -R "${PROJECTS_USER}":"${PROJECTS_USER}" "$PROJECT_DIR"

    find "$PROJECT_DIR/html" -type d -exec chmod 775 {} +
    find "$PROJECT_DIR/html" -type f -exec chmod 664 {} +

    echo "🔐 _ops access configuration..."
    USER=${OPS_USER:-admin}
    PASS=${OPS_PASS:-admin}
    htpasswd -b -c "$PROJECT_DIR/html/ops/.htpasswd" "$USER" "$PASS"
    chown www-data:www-data "$PROJECT_DIR/html/ops/.htpasswd"
    chmod 640 "$PROJECT_DIR/html/ops/.htpasswd"

    echo "🔐 Locking down code-signing/upload keys..."
    # Private keys: only boincadm ever needs these (tools.sh signs as boincadm) -> owner-only.
    chmod 600 "$PROJECT_DIR"/keys/*_private 2>/dev/null || true
    # Public keys: the scheduler CGI (runs as www-data, in the boincadm group) reads these on
    # every scheduler request to verify app signatures -> must stay group-readable, or every
    # client request silently fails with "Server can't find key file" and 0 tasks are ever sent.
    chmod 640 "$PROJECT_DIR"/keys/*_public 2>/dev/null || true

    echo "🔐 Locking down config.xml (contains the DB root password)..."
    # www-data needs group-read: the ops PHP pages parse config.xml directly.
    chown "${PROJECTS_USER}":www-data "$PROJECT_DIR/config.xml" 2>/dev/null || true
    chmod 640 "$PROJECT_DIR/config.xml" 2>/dev/null || true

    echo "🔧 Setting permissions for upload/download/logs/pid..."
    chown -R "${PROJECTS_USER}:www-data" "$PROJECT_DIR/upload"
    chown -R "${PROJECTS_USER}:www-data" "$PROJECT_DIR/download"
    chown -R "${PROJECTS_USER}:www-data" "$PROJECT_DIR/log_${SERVER_HOSTNAME}"
    #chown -R "${PROJECTS_USER}:www-data" "$PROJECT_DIR/pid_${SERVER_HOSTNAME}"
    chmod -R 2770 "${PROJECT_DIR}/upload"
    chmod -R 2770 "${PROJECT_DIR}/download"
    chmod -R 2770 "${PROJECT_DIR}/log_${SERVER_HOSTNAME}"
    #chmod -R 2770 "${PROJECT_DIR}/pid_${SERVER_HOSTNAME}"
    
    echo "✅ Permissions successfully configured"
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