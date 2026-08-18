#!/bin/bash
set -e
cd "$(dirname "$0")"

if [ -f ../.env ]; then
    set -a
    . ../.env
    set +a
else
    echo "❌ ERROR: ../.env file not found! Make sure to run this script from the right directory."
    exit 1
fi

PROJECT_DIR="${SERVER_VOLUME_PROJECTS_DIR}/camicia"
KEY_DIR="${SERVER_VOLUME_KEYS_DIR}"

# If anything below fails after daemons are stopped, this leaves the project
# running degraded (down) until camicia.cronjob's 5-minute "bin/start --cron"
# auto-heal happens to notice -- restart immediately instead so a failed
# deploy is loud (you see the actual error) rather than also silently taking
# the project offline for a few minutes.
PROJECT_FOUND=0
recover_daemons_on_failure() {
    local exit_code=$?
    # Unconditional, regardless of success/failure: if the decrypt step below
    # ever ran, this guarantees the plaintext code-signing key can't survive
    # past this script's exit, even if `set -e` kills the script somewhere
    # between decrypting it and the normal cleanup step further down.
    docker exec "$SERVER_CONTAINER_NAME" bash -c "rm -f $KEY_DIR/code_sign_private" 2>/dev/null || true
    if [ "$exit_code" -ne 0 ] && [ "$PROJECT_FOUND" -eq 1 ]; then
        echo "⚠️  Deploy failed (exit $exit_code) -- attempting to restart daemons so the project isn't left down..."
        if docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "cd $PROJECT_DIR && ./bin/start"; then
            echo "   -> Daemons restarted. The deploy itself still failed -- see the error above."
        else
            echo "   -> Could not restart daemons automatically. Run this manually:"
            echo "      docker exec --user $PROJECTS_USER $SERVER_CONTAINER_NAME bash -c 'cd $PROJECT_DIR && ./bin/start'"
        fi
    fi
}
trap recover_daemons_on_failure EXIT

echo "🔍 Checking if project exists in the container..."
if docker exec "$SERVER_CONTAINER_NAME" bash -c "[ -d \"$PROJECT_DIR\" ]"; then
    echo "✅ Project found! Starting deployment..."
    PROJECT_FOUND=1

    # entrypoint.sh only runs this at container boot, and only if the project
    # already existed at that exact moment -- but the documented bootstrap
    # workflow runs make_project *after* the container is already up
    # (README.md SS1-2), so on a fresh project entrypoint.sh's own call is a
    # no-op and www-data never gets read access to html/. Running it here
    # guarantees it happens on every deploy, including the very first one.
    echo "🔐 Fixing project-wide permissions..."
    docker exec \
        -e SERVER_VOLUME_PROJECTS_DIR="$SERVER_VOLUME_PROJECTS_DIR" \
        -e PROJECTS_USER="$PROJECTS_USER" \
        -e OPS_USER="$OPS_USER" \
        -e OPS_PASS="$OPS_PASS" \
        -e SERVER_HOSTNAME="$SERVER_HOSTNAME" \
        "$SERVER_CONTAINER_NAME" /usr/local/bin/fix_permissions.sh

    # Same reasoning/fix as entrypoint.sh: make_project only writes
    # camicia.cronjob to disk, installing it into an actual crontab is a
    # manual bootstrap step easy to miss -- redone here too so an image that
    # predates this fix (running container, not yet recreated) self-heals on
    # the very next deploy rather than needing a full container recreate.
    if docker exec "$SERVER_CONTAINER_NAME" bash -c "[ -f '$PROJECT_DIR/camicia.cronjob' ]"; then
        docker exec "$SERVER_CONTAINER_NAME" bash -c "crontab -u '$PROJECTS_USER' '$PROJECT_DIR/camicia.cronjob'"
    fi

    echo "🛑 Stopping BOINC project daemons..."
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "cd $PROJECT_DIR && ./bin/stop"

    echo "📂 Copying files and folders to the container..."
    docker cp ./assimilator "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/"
    docker cp ./worker "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/"
    docker cp ./work_generator "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/"
    docker cp ./templates "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/"
    docker cp ./project.xml "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/"
    docker cp ./project.inc "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/project/project.inc"
    docker cp ./signup.php "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/user/signup.php"
    docker cp ./terms_of_use.txt "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/terms_of_use.txt"
    docker cp ./db_backup.sh "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/bin/db_backup.sh"
    docker cp ./disk_space_check.sh "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/bin/disk_space_check.sh"
    docker cp ./memory_check.sh "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/bin/memory_check.sh"
    docker cp ./notify.sh "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/bin/notify.sh"
    docker cp ./rotate_results.sh "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/bin/rotate_results.sh"
    docker cp ./rotate_daemon_logs.sh "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/bin/rotate_daemon_logs.sh"

    echo "📧 Vendoring PHPMailer..."
    docker exec "$SERVER_CONTAINER_NAME" bash -c "mkdir -p '$PROJECT_DIR/html/inc/PHPMailer'"
    docker cp ./phpmailer/src "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/inc/PHPMailer/src"

    # Deployed to html/ops/ (not bin/), matching upstream's own placement of
    # create_forums.php -- its require_once("../inc/forum_db.inc") is a
    # relative path that only resolves correctly one level under html/.
    docker cp ./create_forums.php "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/ops/create_forums.php"

    # ntfy.sh topic for disk_space_check.sh/memory_check.sh push alerts --
    # optional, only written if NTFY_TOPIC is set in .env. Kept out of the
    # repo (it's effectively a shared secret: anyone with it can post to or
    # read the topic) the same way OPS_PASS/DB credentials are.
    if [ -n "$NTFY_TOPIC" ]; then
        docker exec "$SERVER_CONTAINER_NAME" bash -c "echo '$NTFY_TOPIC' > $PROJECT_DIR/ntfy_topic"
    fi

    # SMTP credentials for make_php_mailer() (see project.inc) -- optional,
    # only written if SMTP_HOST is set in .env. Kept out of the repo the same
    # way NTFY_TOPIC/OPS_PASS are; piped over stdin (not passed as a docker
    # exec argument) so the password doesn't appear in `docker top`/process
    # listings while this runs.
    if [ -n "$SMTP_HOST" ]; then
        docker exec -i "$SERVER_CONTAINER_NAME" bash -c "cat > $PROJECT_DIR/smtp_credentials.inc.php" <<EOF
<?php
define('SMTP_HOST', '$SMTP_HOST');
define('SMTP_PORT', $SMTP_PORT);
define('SMTP_USERNAME', '$SMTP_USERNAME');
define('SMTP_PASSWORD', '$SMTP_PASSWORD');
define('SMTP_FROM_EMAIL', '$SMTP_FROM_EMAIL');
define('SMTP_FROM_NAME', '$SMTP_FROM_NAME');
EOF
    fi

    echo "🧩 Preparing smart merge of config.xml..."
    docker cp ./config.xml "$SERVER_CONTAINER_NAME":/tmp/config_new.xml
    docker cp ./merge_config.py "$SERVER_CONTAINER_NAME":/tmp/merge_config.py

    # Akismet key for forum/PM/profile spam filtering -- optional, only
    # injected if AKISMET_KEY is set in .env. Added to the staged
    # /tmp/config_new.xml (not the committed tools/config.xml) so it flows
    # through merge_config.py's existing per-child <config> merge unchanged,
    # and passed over stdin rather than as a docker exec argument so it
    # doesn't appear in `docker top`/process listings while this runs (same
    # reasoning as the SMTP credentials heredoc below). Writes to a fresh
    # temp file and mv's it over the target rather than opening the just-
    # docker-cp'd file in place for write -- doing the latter directly hit a
    # spurious PermissionError (even as root) on Docker Desktop's WSL2
    # backend, most likely a propagation-timing quirk between docker cp's
    # write and a docker exec'd process re-opening that same inode
    # immediately after; rename-over sidesteps it.
    if [ -n "$AKISMET_KEY" ]; then
        echo "🔑 Injecting Akismet spam-filter key into config.xml..."
        docker exec -i "$SERVER_CONTAINER_NAME" python3 -c "
import sys, xml.etree.ElementTree as ET
key = sys.stdin.read().strip()
tree = ET.parse('/tmp/config_new.xml')
root = tree.getroot()
config = root.find('config')
node = config.find('akismet_key')
if node is None:
    node = ET.SubElement(config, 'akismet_key')
node.text = key
tree.write('/tmp/config_new.xml.tmp', encoding='utf-8', xml_declaration=False)
" <<< "$AKISMET_KEY"
        docker exec "$SERVER_CONTAINER_NAME" mv /tmp/config_new.xml.tmp /tmp/config_new.xml
    fi

    # reCAPTCHA v2 keys (html/inc/recaptchalib.inc, wired into signup/team-
    # create/profile/account-ownership via html/inc/util_basic.inc's
    # project_config_val()) -- same injection pattern as the Akismet key
    # above, for the same reasons. Gated on RECAPTCHA_SITE_KEY alone (not
    # required separately per key) because setting only one half is a real
    # footgun, not a valid partial config: the site key alone renders the
    # checkbox widget but util_basic.inc's project_config_val() returns null
    # for the missing private key, so user_util.inc's
    # `if (recaptcha_private_key())` guard is skipped entirely and every
    # submission is silently accepted unchecked -- decoration, not
    # protection. Both keys are therefore only ever written as a pair.
    if [ -n "$RECAPTCHA_SITE_KEY" ]; then
        echo "🤖 Injecting reCAPTCHA keys into config.xml..."
        docker exec -i "$SERVER_CONTAINER_NAME" python3 -c "
import sys, xml.etree.ElementTree as ET
site_key, secret_key = sys.stdin.read().split('\n', 1)
tree = ET.parse('/tmp/config_new.xml')
root = tree.getroot()
config = root.find('config')
for tag, value in [('recaptcha_public_key', site_key), ('recaptcha_private_key', secret_key.strip())]:
    node = config.find(tag)
    if node is None:
        node = ET.SubElement(config, tag)
    node.text = value
tree.write('/tmp/config_new.xml.tmp', encoding='utf-8', xml_declaration=False)
" <<< "$RECAPTCHA_SITE_KEY
$RECAPTCHA_SECRET_KEY"
        docker exec "$SERVER_CONTAINER_NAME" mv /tmp/config_new.xml.tmp /tmp/config_new.xml
    fi

    echo "🐍 Running Python script to update XML nodes..."
    docker exec "$SERVER_CONTAINER_NAME" python3 /tmp/merge_config.py

    echo "🔐 Fixing permissions for user $PROJECTS_USER..."
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown -R $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/assimilator $PROJECT_DIR/worker $PROJECT_DIR/work_generator $PROJECT_DIR/templates $PROJECT_DIR/*.xml $PROJECT_DIR/html/project/project.inc $PROJECT_DIR/html/user/signup.php $PROJECT_DIR/terms_of_use.txt $PROJECT_DIR/html/inc/PHPMailer 2>/dev/null"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/html/ops/create_forums.php && chmod +x $PROJECT_DIR/html/ops/create_forums.php"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/bin/db_backup.sh && chmod +x $PROJECT_DIR/bin/db_backup.sh"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/bin/disk_space_check.sh && chmod +x $PROJECT_DIR/bin/disk_space_check.sh"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/bin/memory_check.sh && chmod +x $PROJECT_DIR/bin/memory_check.sh"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/bin/notify.sh && chmod +x $PROJECT_DIR/bin/notify.sh"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/bin/rotate_results.sh && chmod +x $PROJECT_DIR/bin/rotate_results.sh"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/bin/rotate_daemon_logs.sh && chmod +x $PROJECT_DIR/bin/rotate_daemon_logs.sh"
    if [ -n "$NTFY_TOPIC" ]; then
        docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/ntfy_topic && chmod 600 $PROJECT_DIR/ntfy_topic"
    fi
    if [ -n "$SMTP_HOST" ]; then
        # www-data needs group-read: project.inc's make_php_mailer() runs as
        # www-data and require_once()s this file directly (same treatment as
        # config.xml, for the same reason).
        docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:www-data $PROJECT_DIR/smtp_credentials.inc.php && chmod 640 $PROJECT_DIR/smtp_credentials.inc.php"
    fi

    echo "⚙️ Compiling Assimilator..."
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "g++ -O3 \
    $PROJECT_DIR/assimilator/assimilator.cpp \
    /usr/local/src/boinc/sched/validate_util.cpp \
    /usr/local/src/boinc/sched/assimilator.cpp \
    -o $PROJECT_DIR/bin/assimilator \
    -I/usr/local/src/boinc \
    -I/usr/local/src/boinc/api \
    -I/usr/local/src/boinc/lib \
    -I/usr/local/src/boinc/sched \
    -I/usr/local/src/boinc/db \
    -I/usr/include/mysql \
    -I/usr/include/mariadb \
    -L/usr/local/src/boinc/lib \
    -L/usr/local/src/boinc/sched \
    /usr/local/src/boinc/sched/libsched.a \
    /usr/local/src/boinc/api/libboinc_api.a \
    /usr/local/src/boinc/lib/libboinc.a \
    -lmysqlclient -pthread -ldl"

    echo "⚙️ Compiling Worker..."
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "shopt -s nullglob && g++ -O3 -static \
    $PROJECT_DIR/worker/worker.cpp \
    $PROJECT_DIR/worker/core/*.cpp \
    -o $PROJECT_DIR/worker/worker_app \
    -I/usr/local/src/boinc/api \
    -I/usr/local/src/boinc/lib \
    -I$PROJECT_DIR/worker \
    -I$PROJECT_DIR/worker/core \
    /usr/local/src/boinc/api/libboinc_api.a \
    /usr/local/src/boinc/lib/libboinc.a \
    -pthread -ldl"

    echo "⚙️ Compiling Work Generator..."
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "g++ -O3 \
    $PROJECT_DIR/work_generator/work_generator.cpp \
    -o $PROJECT_DIR/bin/work_generator \
    -I/usr/local/src/boinc \
    -I/usr/local/src/boinc/api \
    -I/usr/local/src/boinc/lib \
    -I/usr/local/src/boinc/sched \
    -I/usr/local/src/boinc/db \
    -I/usr/local/src/boinc/tools \
    -I/usr/include/mysql \
    -I/usr/include/mariadb \
    -I$PROJECT_DIR/worker/core \
    -L/usr/local/src/boinc/lib \
    -L/usr/local/src/boinc/sched \
    /usr/local/src/boinc/sched/libsched.a \
    /usr/local/src/boinc/lib/libboinc_crypt.a \
    /usr/local/src/boinc/api/libboinc_api.a \
    /usr/local/src/boinc/lib/libboinc.a \
    -lmysqlclient -lcrypto -lssl -pthread -ldl"

    echo "🔄 Applying configuration changes (xadd)..."
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "cd $PROJECT_DIR && ./bin/xadd"

    echo "🏷️ Determining next app version..."
    APP_NAME="simulator"
    PLATFORM="x86_64-pc-linux-gnu"
    APP_DIR="$PROJECT_DIR/apps/$APP_NAME"

    LAST_VERSION=$(docker exec "$SERVER_CONTAINER_NAME" bash -c \
        "ls -1 '$APP_DIR' 2>/dev/null | grep -E '^[0-9]+\.[0-9]+\$' | sort -t. -k1,1n -k2,2n | tail -1")

    if [ -z "$LAST_VERSION" ]; then
        NEW_VERSION="1.00"
    else
        MAJOR="${LAST_VERSION%.*}"
        MINOR="${LAST_VERSION#*.}"
        MINOR=$((10#$MINOR + 1))
        if [ "$MINOR" -ge 100 ]; then MAJOR=$((MAJOR + 1)); MINOR=0; fi
        NEW_VERSION=$(printf "%d.%02d" "$MAJOR" "$MINOR")
    fi

    VERSION_DIR="$APP_DIR/$NEW_VERSION/$PLATFORM"
    # BOINC treats download/<physical_name> as immutable once a client has
    # fetched it: update_versions refuses to re-stage a same-named file whose
    # bytes differ ("BOINC files are immutable"). Reusing a fixed physical
    # name like "worker_app" across every version therefore silently breaks
    # registration the moment worker.cpp's compiled output actually changes
    # between deploys, which is the normal case, not an edge case. Version-
    # qualify the physical name so every deploy gets a filename BOINC has
    # never served before.
    PHYSICAL_NAME="worker_app_$NEW_VERSION"
    echo "   -> New version: $NEW_VERSION ($VERSION_DIR, $PHYSICAL_NAME)"

    echo "📦 Staging new app version..."
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "mkdir -p '$VERSION_DIR'"
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c \
        "cp '$PROJECT_DIR/worker/worker_app' '$VERSION_DIR/$PHYSICAL_NAME'"
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c \
        "cat > '$VERSION_DIR/version.xml' <<EOF
<version>
    <file>
        <physical_name>$PHYSICAL_NAME</physical_name>
        <main_program/>
    </file>
</version>
EOF"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown -R $PROJECTS_USER:$PROJECTS_USER '$APP_DIR'"

    # code_sign_private lives on its own persistent bind mount (KEY_DIR, not
    # PROJECT_DIR -- see config.xml's <key_dir>) and is kept encrypted at
    # rest (code_sign_private.gpg) per BOINC's own code-signing wiki
    # guidance, decrypted only for the brief window update_versions
    # actually needs it to sign. Passphrase comes from
    # CODE_SIGN_KEY_PASSPHRASE -- on production this is injected by
    # deploy.yml from a GitHub Actions Environment secret and never touches
    # .env/disk there; locally it can be set in .env for convenience. If
    # unset, this is skipped entirely and a plaintext key must already
    # exist, or update_versions below fails with BOINC's own clear
    # "no code signing private key" error rather than anything silent.
    # Passphrase piped over stdin, not a docker exec argument, so it doesn't
    # appear in process listings while this runs (same reasoning as the
    # Akismet key/SMTP credentials elsewhere in this script).
    if [ -n "$CODE_SIGN_KEY_PASSPHRASE" ]; then
        echo "🔓 Decrypting code-signing key for this deploy..."
        docker exec -i "$SERVER_CONTAINER_NAME" bash -c \
            "gpg --batch --yes --passphrase-fd 0 -o $KEY_DIR/code_sign_private --decrypt $KEY_DIR/code_sign_private.gpg" \
            <<< "$CODE_SIGN_KEY_PASSPHRASE"
        docker exec "$SERVER_CONTAINER_NAME" bash -c \
            "chown $PROJECTS_USER:$PROJECTS_USER $KEY_DIR/code_sign_private && chmod 600 $KEY_DIR/code_sign_private"
    fi

    echo "🔄 Registering new app version (update_versions)..."
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c \
        "cd $PROJECT_DIR && ./bin/update_versions --noconfirm"

    if [ -n "$CODE_SIGN_KEY_PASSPHRASE" ]; then
        echo "🔒 Re-hiding code-signing key..."
        docker exec "$SERVER_CONTAINER_NAME" bash -c "rm -f $KEY_DIR/code_sign_private"
    fi

    echo "▶️ Restarting BOINC project..."
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "cd $PROJECT_DIR && ./bin/start"

    docker exec "$SERVER_CONTAINER_NAME" bash -c "rm -f /tmp/config_new.xml /tmp/merge_config.py"
    
    echo "🚀 Deployment and compilation completed successfully!"
else
    echo "❌ ERROR: Folder $PROJECT_DIR does not exist inside container $SERVER_CONTAINER_NAME."
    echo "   Make sure the container is running and the path is correct."
    exit 1
fi