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
    # Everything under tools/html/ mirrors its real html/ destination path
    # exactly (tools/html/user/about.php -> html/user/about.php, etc.), so
    # the source path here doubles as the deploy-target documentation --
    # no need to cross-reference this list to know where a given file lands.
    docker cp ./html/project/project.inc "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/project/project.inc"
    docker cp ./html/project/project_description.php "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/project/project_description.php"
    docker cp ./html/project/project_specific_prefs.inc "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/project/project_specific_prefs.inc"
    docker cp ./html/user/about.php "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/user/about.php"
    # Homepage banner (project_banner() in project.inc) -- replaces make_project's
    # stock placeholder (img/water.jpg, never deployed/tracked by us) with an
    # SVG matching landing/index.html's own felt/gold/cream card-table palette.
    docker cp ./html/user/img/camicia_banner.svg "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/user/img/camicia_banner.svg"
    # Browser tab icon (SHORTCUT_ICON in project.inc) -- same felt/gold/cream
    # palette as the banner above: a shirt collar (the literal meaning of
    # "camicia") with a diamond pip nested in the neckline and two buttons
    # down the front.
    docker cp ./html/user/img/favicon.svg "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/user/img/favicon.svg"
    # All four below are byte-identical copies of BOINC's own files, each
    # with the same one-line-becomes-two-lines fix: get_cached_data() can
    # return null on a cold/expired cache, and passing null to
    # unserialize() is a deprecation notice as of PHP 8.1 -- found on
    # server_status.php's live output, then found to be the same latent
    # bug in these other three (not yet triggered there, same root cause).
    docker cp ./html/user/server_status.php "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/user/server_status.php"
    docker cp ./html/user/download_network.php "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/user/download_network.php"
    docker cp ./html/user/get_project_config.php "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/user/get_project_config.php"
    docker cp ./html/user/team_members.php "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/user/team_members.php"
    # Same PHP 8.1 deprecation class, this time xml_parse(null,...) instead
    # of unserialize(null) -- $prefs_xml is null for a user who's never
    # saved custom prefs. Found live on prefs.php.
    docker cp ./html/inc/prefs.inc "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/inc/prefs.inc"
    docker cp ./html/inc/prefs_project.inc "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/inc/prefs_project.inc"
    # Byte-identical copy of BOINC's own file, with a targeted fix to the
    # SHORTCUT_ICON block (see util.inc's own comment): prepends $url_base
    # like STYLESHEET/STYLESHEET2 already do, and emits the real MIME type
    # instead of always claiming image/x-icon.
    docker cp ./html/inc/util.inc "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/inc/util.inc"
    docker cp ./html/user/signup.php "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/user/signup.php"
    docker cp ./terms_of_use.txt "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/terms_of_use.txt"
    # translation.inc is a byte-identical copy of BOINC's own file plus one
    # hook (see tools/html/inc/translation.inc's own header) that loads
    # translation_fixes.inc after every per-request compiled language file,
    # so specific broken upstream strings can be corrected without ever
    # touching/tracking whole language files -- see that file for why.
    docker cp ./html/inc/translation.inc "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/inc/translation.inc"
    docker cp ./html/languages/compiled/translation_fixes.inc "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/languages/compiled/translation_fixes.inc"
    docker cp ./db_backup.sh "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/bin/db_backup.sh"
    docker cp ./disk_space_check.sh "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/bin/disk_space_check.sh"
    docker cp ./memory_check.sh "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/bin/memory_check.sh"
    docker cp ./notify.sh "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/bin/notify.sh"
    docker cp ./rotate_results.sh "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/bin/rotate_results.sh"
    docker cp ./rotate_daemon_logs.sh "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/bin/rotate_daemon_logs.sh"
    docker cp ./backup_offsite_gdrive.sh "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/bin/backup_offsite_gdrive.sh"
    docker cp ./backup_usb.sh "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/bin/backup_usb.sh"

    echo "📧 Vendoring PHPMailer..."
    docker exec "$SERVER_CONTAINER_NAME" bash -c "mkdir -p '$PROJECT_DIR/html/inc/PHPMailer'"
    docker cp ./html/inc/PHPMailer/src "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/inc/PHPMailer/src"

    # Deployed to html/ops/ (not bin/), matching upstream's own placement of
    # create_forums.php -- its require_once("../inc/forum_db.inc") is a
    # relative path that only resolves correctly one level under html/.
    docker cp ./html/ops/create_forums.php "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/ops/create_forums.php"

    # ntfy.sh topic for disk_space_check.sh/memory_check.sh push alerts --
    # optional, only written if NTFY_TOPIC is set in .env. Kept out of the
    # repo (it's effectively a shared secret: anyone with it can post to or
    # read the topic) the same way OPS_PASS/DB credentials are.
    if [ -n "$NTFY_TOPIC" ]; then
        docker exec "$SERVER_CONTAINER_NAME" bash -c "echo '$NTFY_TOPIC' > $PROJECT_DIR/ntfy_topic"
    fi

    # RCLONE_CONFIG_PASS for backup_offsite_gdrive.sh's daily cron run --
    # unlike CODE_SIGN_KEY_PASSPHRASE (only ever needed transiently, during
    # this very deploy run, for update_versions), the offsite backup runs
    # independently every day via bin/start --cron, long after this
    # deploy.yml process has exited -- so the passphrase has to be written
    # somewhere the cron job can read it later, not just left in this
    # script's own process environment. Written to KEY_DIR (not PROJECT_DIR)
    # so it lives alongside rclone.conf in the same persistent volume that
    # survives a project reset. Piped over stdin, not a docker exec
    # argument, so it doesn't appear in `docker top`/process listings.
    if [ -n "$RCLONE_CONFIG_PASS" ]; then
        docker exec -i "$SERVER_CONTAINER_NAME" bash -c "cat > $KEY_DIR/rclone_config_pass" <<< "$RCLONE_CONFIG_PASS"
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
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown -R $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/assimilator $PROJECT_DIR/worker $PROJECT_DIR/work_generator $PROJECT_DIR/templates $PROJECT_DIR/*.xml $PROJECT_DIR/html/project/project.inc $PROJECT_DIR/html/project/project_description.php $PROJECT_DIR/html/project/project_specific_prefs.inc $PROJECT_DIR/html/user/signup.php $PROJECT_DIR/html/user/about.php $PROJECT_DIR/html/user/img/camicia_banner.svg $PROJECT_DIR/html/user/img/favicon.svg $PROJECT_DIR/html/user/server_status.php $PROJECT_DIR/html/user/download_network.php $PROJECT_DIR/html/user/get_project_config.php $PROJECT_DIR/html/user/team_members.php $PROJECT_DIR/html/inc/prefs.inc $PROJECT_DIR/html/inc/prefs_project.inc $PROJECT_DIR/html/inc/util.inc $PROJECT_DIR/terms_of_use.txt $PROJECT_DIR/html/inc/PHPMailer $PROJECT_DIR/html/inc/translation.inc $PROJECT_DIR/html/languages/compiled/translation_fixes.inc 2>/dev/null"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/html/ops/create_forums.php && chmod +x $PROJECT_DIR/html/ops/create_forums.php"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/bin/db_backup.sh && chmod +x $PROJECT_DIR/bin/db_backup.sh"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/bin/disk_space_check.sh && chmod +x $PROJECT_DIR/bin/disk_space_check.sh"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/bin/memory_check.sh && chmod +x $PROJECT_DIR/bin/memory_check.sh"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/bin/notify.sh && chmod +x $PROJECT_DIR/bin/notify.sh"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/bin/rotate_results.sh && chmod +x $PROJECT_DIR/bin/rotate_results.sh"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/bin/rotate_daemon_logs.sh && chmod +x $PROJECT_DIR/bin/rotate_daemon_logs.sh"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/bin/backup_offsite_gdrive.sh && chmod +x $PROJECT_DIR/bin/backup_offsite_gdrive.sh"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/bin/backup_usb.sh && chmod +x $PROJECT_DIR/bin/backup_usb.sh"
    if [ -n "$NTFY_TOPIC" ]; then
        docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/ntfy_topic && chmod 600 $PROJECT_DIR/ntfy_topic"
    fi
    if [ -n "$RCLONE_CONFIG_PASS" ]; then
        docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $KEY_DIR/rclone_config_pass && chmod 600 $KEY_DIR/rclone_config_pass"
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

    echo "⚙️ Compiling Worker (Windows)..."
    # Cross-compiled with mingw-w64 against the separate boinc-win build
    # (images/server/Dockerfile) so the worker also ships for windows_x86_64,
    # the largest BOINC volunteer platform. No -ldl (no libdl on Windows) and
    # the physical file must end in .exe for Windows to actually execute it
    # once downloaded -- BOINC's own <main_program/> marker in version.xml
    # doesn't care about the name, but the OS does.
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "shopt -s nullglob && x86_64-w64-mingw32-g++ -O3 -static \
    $PROJECT_DIR/worker/worker.cpp \
    $PROJECT_DIR/worker/core/*.cpp \
    -o $PROJECT_DIR/worker/worker_app.exe \
    -I/usr/local/src/boinc-win/api \
    -I/usr/local/src/boinc-win/lib \
    -I$PROJECT_DIR/worker \
    -I$PROJECT_DIR/worker/core \
    /usr/local/src/boinc-win/api/libboinc_api.a \
    /usr/local/src/boinc-win/lib/libboinc.a \
    -pthread"

    echo "⚙️ Compiling Worker (Linux ARM64)..."
    # Cross-compiled with gcc-aarch64-linux-gnu against the separate
    # boinc-arm64 build (images/server/Dockerfile) so the worker also ships
    # for aarch64-unknown-linux-gnu. Named worker_app_arm64 (not worker_app)
    # since both land in the same $PROJECT_DIR/worker/ directory as the
    # native x86_64 build.
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "shopt -s nullglob && aarch64-linux-gnu-g++ -O3 -static \
    $PROJECT_DIR/worker/worker.cpp \
    $PROJECT_DIR/worker/core/*.cpp \
    -o $PROJECT_DIR/worker/worker_app_arm64 \
    -I/usr/local/src/boinc-arm64/api \
    -I/usr/local/src/boinc-arm64/lib \
    -I$PROJECT_DIR/worker \
    -I$PROJECT_DIR/worker/core \
    /usr/local/src/boinc-arm64/api/libboinc_api.a \
    /usr/local/src/boinc-arm64/lib/libboinc.a \
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

    echo "⚙️ Compiling antique_file_deleter..."
    # Stock BOINC source (see images/server/Dockerfile's BOINC_COMMIT) --
    # upstream fixed the errno/readdir bug we used to carry as a local
    # patch (tools/antique_file_deleter.cpp, removed), so this now just
    # rebuilds and redeploys the real thing on every deploy, same pattern
    # as worker/assimilator/work_generator, keeping it in sync with
    # whatever BOINC_COMMIT is pinned instead of the local patch drifting
    # from upstream.
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "g++ -O3 \
    /usr/local/src/boinc/sched/antique_file_deleter.cpp \
    -o $PROJECT_DIR/bin/antique_file_deleter \
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
    /usr/local/src/boinc/lib/libboinc_crypt.a \
    /usr/local/src/boinc/api/libboinc_api.a \
    /usr/local/src/boinc/lib/libboinc.a \
    -lmysqlclient -lcrypto -lssl -pthread -ldl"

    echo "🔄 Applying configuration changes (xadd)..."
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "cd $PROJECT_DIR && ./bin/xadd"

    echo "🏷️ Determining next app version..."
    APP_NAME="simulator"
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
    echo "   -> New version: $NEW_VERSION"

    # Staged together, same version number, one update_versions call below
    # registers both -- matches BOINC's own convention for shipping multiple
    # platforms per version, and means the existing code-signing step (already
    # unconditional) signs both files with no per-platform logic needed.
    echo "📦 Staging new app version..."
    for ENTRY in "x86_64-pc-linux-gnu:worker_app:worker_app_$NEW_VERSION" "windows_x86_64:worker_app.exe:worker_app_$NEW_VERSION.exe" "aarch64-unknown-linux-gnu:worker_app_arm64:worker_app_arm64_$NEW_VERSION" "arm64-apple-darwin:worker_app_macos:worker_app_macos_$NEW_VERSION"; do
        PLATFORM="${ENTRY%%:*}"
        REST="${ENTRY#*:}"
        SOURCE_BINARY="${REST%%:*}"
        PHYSICAL_NAME="${REST#*:}"
        VERSION_DIR="$APP_DIR/$NEW_VERSION/$PLATFORM"
        echo "   -> $PLATFORM: $VERSION_DIR/$PHYSICAL_NAME"

        # BOINC treats download/<physical_name> as immutable once a client has
        # fetched it: update_versions refuses to re-stage a same-named file
        # whose bytes differ ("BOINC files are immutable"). Reusing a fixed
        # physical name like "worker_app" across every version therefore
        # silently breaks registration the moment worker.cpp's compiled output
        # actually changes between deploys, which is the normal case, not an
        # edge case. Version-qualify the physical name so every deploy gets a
        # filename BOINC has never served before.
        docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "mkdir -p '$VERSION_DIR'"
        docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c \
            "cp '$PROJECT_DIR/worker/$SOURCE_BINARY' '$VERSION_DIR/$PHYSICAL_NAME'"
        docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c \
            "cat > '$VERSION_DIR/version.xml' <<EOF
<version>
    <file>
        <physical_name>$PHYSICAL_NAME</physical_name>
        <main_program/>
    </file>
</version>
EOF"
    done
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

    # upload_private, unlike code_sign_private, must stay plaintext
    # continuously -- confirmed the hard way 2026-08-20: sched/transitioner.cpp
    # (a daemon meant to run indefinitely) reads it unconditionally at
    # startup regardless of dont_generate_upload_certificates/
    # ignore_upload_certificates, and crash-loops with "can't read key" if
    # it's missing. (get_file.cpp/file_upload_handler, the actual client
    # upload path, DOES check that flag first and correctly never needs this
    # file on this project -- only transitioner is unconditional.) So this is
    # a self-healing *restore*, not a decrypt-use-re-hide cycle: only runs if
    # the plaintext doesn't already exist, using the encrypted upload_private.gpg
    # backup + UPLOAD_KEY_PASSPHRASE (same GitHub Actions Environment secret
    # injection pattern as CODE_SIGN_KEY_PASSPHRASE). fix_permissions.sh's
    # existing "$KEY_DIR"/*_private glob already locks down the resulting
    # file to 600 on this same deploy, no extra chmod needed here.
    if [ -n "$UPLOAD_KEY_PASSPHRASE" ]; then
        if ! docker exec "$SERVER_CONTAINER_NAME" bash -c "[ -f $KEY_DIR/upload_private ]"; then
            echo "🔓 Restoring missing upload_private from encrypted backup..."
            docker exec -i "$SERVER_CONTAINER_NAME" bash -c \
                "gpg --batch --yes --passphrase-fd 0 -o $KEY_DIR/upload_private --decrypt $KEY_DIR/upload_private.gpg" \
                <<< "$UPLOAD_KEY_PASSPHRASE"
            docker exec "$SERVER_CONTAINER_NAME" bash -c \
                "chown $PROJECTS_USER:$PROJECTS_USER $KEY_DIR/upload_private && chmod 600 $KEY_DIR/upload_private"
        fi
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