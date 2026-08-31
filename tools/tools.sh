#!/bin/bash
set -e
cd "$(dirname "$0")"

# Delegates to deploy_rollback.sh's own restore path -- the same one the
# failure trap below uses -- so a deploy that succeeded but left the
# project unhealthy (caught by deploy.yml's post-health-check step, not by
# anything in this script) can be rolled back with the exact same logic,
# not a second copy of it.
if [ "$1" == "--restore" ]; then
    exec bash ./deploy_rollback.sh --restore
fi

if [ -f ../.env ]; then
    set -a
    . ../.env
    set +a
else
    echo "❌ ERROR: ../.env file not found! Make sure to run this script from the right directory."
    exit 1
fi

. ./notify.sh

PROJECT_DIR="${SERVER_VOLUME_PROJECTS_DIR}/camicia"
KEY_DIR="${SERVER_VOLUME_KEYS_DIR}"

# If anything below fails after daemons are stopped, this leaves the project
# running degraded (down) until camicia.cronjob's 5-minute "bin/start --cron"
# auto-heal happens to notice -- restart immediately instead so a failed
# deploy is loud (you see the actual error) rather than also silently taking
# the project offline for a few minutes.
PROJECT_FOUND=0
# Set once deploy_rollback.sh --backup has actually run (right after
# daemons stop, before the destructive docker cp block) -- before that
# point there's nothing to roll back to yet, so a failure still falls back
# to the plain daemon-restart this always did.
BACKUP_TAKEN=0
# Updated before each major stage -- included in the failure notification
# below (and passed through to deploy_rollback.sh --restore) so an alert
# says what actually broke, not just "deploy failed". Cosmetic only: never
# read to make a recovery decision, that's what published_version (see
# publish_version.sh) is for.
CURRENT_STAGE="starting deploy"
recover_daemons_on_failure() {
    local exit_code=$?
    # Guarded on CODE_SIGN_KEY_PASSPHRASE, not unconditional: only decrypted
    # keys/code_sign_private.gpg into a plaintext code_sign_private if this
    # was set (see publish_version.sh), so only then is there anything to
    # clean up, and only then does an encrypted backup exist to restore a
    # fresh plaintext copy from on the next run. Without this guard, the
    # "no passphrase set, a plaintext code_sign_private must already exist"
    # local-dev/staging convenience mode (see .env.example) would have its
    # only copy of the key deleted the moment this script exits, with
    # nothing able to regenerate it short of a full make_project re-bootstrap.
    if [ -n "$CODE_SIGN_KEY_PASSPHRASE" ]; then
        docker exec "$SERVER_CONTAINER_NAME" bash -c "rm -f $KEY_DIR/code_sign_private" 2>/dev/null || true
    fi
    if [ "$exit_code" -ne 0 ] && [ "$PROJECT_FOUND" -eq 1 ]; then
        if [ "$BACKUP_TAKEN" -eq 1 ]; then
            echo "⚠️  Deploy failed during '$CURRENT_STAGE' (exit $exit_code) -- rolling back to the pre-deploy state..."
            CURRENT_STAGE="$CURRENT_STAGE" bash ./deploy_rollback.sh --restore || true
        else
            echo "⚠️  Deploy failed during '$CURRENT_STAGE' (exit $exit_code) -- attempting to restart daemons so the project isn't left down..."
            if docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "cd $PROJECT_DIR && ./bin/start"; then
                echo "   -> Daemons restarted. The deploy itself still failed -- see the error above."
                notify "Camicia: deploy FAILED" "Failed during '$CURRENT_STAGE' (exit $exit_code), before any file backup was taken -- daemons restarted on the pre-existing state, nothing was changed" "high"
            else
                echo "   -> Could not restart daemons automatically. Run this manually:"
                echo "      docker exec --user $PROJECTS_USER $SERVER_CONTAINER_NAME bash -c 'cd $PROJECT_DIR && ./bin/start'"
                notify "Camicia: deploy FAILED, daemons DOWN" "Failed during '$CURRENT_STAGE' (exit $exit_code), before any file backup was taken, and daemons did not restart -- needs manual intervention" "high"
            fi
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
    CURRENT_STAGE="fixing permissions"
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

    CURRENT_STAGE="stopping daemons"
    echo "🛑 Stopping BOINC project daemons..."
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "cd $PROJECT_DIR && ./bin/stop"

    CURRENT_STAGE="backing up pre-deploy state"
    echo "💾 Backing up pre-deploy state..."
    bash ./deploy_rollback.sh --backup
    BACKUP_TAKEN=1

    CURRENT_STAGE="copying files into the container"
    echo "📂 Copying files and folders to the container..."
    docker cp ./assimilator "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/"
    docker cp ./worker "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/"
    docker cp ./work_generator "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/"
    # assimilate_handler() spawns bin/verify_sample as its authoritative
    # pre-canonicalization check -- see assimilator.cpp's own header
    # comment on run_verify_sample() for why. Source lands here for
    # publish_version.sh to compile from, same as assimilator/worker/
    # work_generator above.
    docker cp ./verify_sample "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/"
    docker cp ./templates "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/"
    docker cp ./project.xml "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/"
    # Tells bin/db_dump (config.xml's already-enabled 24h task) which tables
    # to export and where -- tracked here instead of relying on whatever
    # make_project may or may not have written once, directly, into the
    # live container.
    docker cp ./db_dump_spec.xml "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/db_dump_spec.xml"
    # html/stats/ (db_dump's final_output_dir) sits outside html/user/, the
    # only directory Apache actually serves (camicia.httpd.conf) -- this
    # symlink is what makes the exported *.gz files reachable at
    # <master_url>/stats/ at all. FollowSymLinks is already on for
    # html/user/, so no Apache config change needed. -sfn: safe to rerun
    # every deploy, never fails on an already-existing link/directory.
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c \
        "mkdir -p '$PROJECT_DIR/html/stats' && ln -sfn ../stats '$PROJECT_DIR/html/user/stats'"
    # Everything under tools/html/ mirrors its real html/ destination path
    # exactly (tools/html/user/about.php -> html/user/about.php, etc.), so
    # the source path here doubles as the deploy-target documentation --
    # no need to cross-reference this list to know where a given file lands.
    docker cp ./html/project/project.inc "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/project/project.inc"
    docker cp ./html/project/project_description.php "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/project/project_description.php"
    docker cp ./html/project/project_specific_prefs.inc "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/project/project_specific_prefs.inc"
    docker cp ./html/user/about.php "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/user/about.php"
    docker cp ./html/user/privacy.php "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/user/privacy.php"
    # Certificate: restyled to match the site's felt/gold/cream identity,
    # plus a verification code/link -- see html/inc/cert.inc.
    docker cp ./html/user/cert1.php "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/user/cert1.php"
    docker cp ./html/inc/cert.inc "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/inc/cert.inc"
    docker cp ./html/user/verify_cert.php "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/user/verify_cert.php"
    # Public progress page -- reads progress_stats.json and the two
    # records_*.txt files below, never queries the DB itself (see
    # generate_progress_stats.php's header comment for why).
    docker cp ./html/user/progress.php "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/user/progress.php"
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
    # Byte-identical copy of BOINC's own file, with one fix: create_forum()'s
    # INSERT never sets orderID, which is NOT NULL with no default -- fine
    # under a lenient SQL mode, an uncaught mysqli_sql_exception under
    # STRICT_TRANS_TABLES (this DB's actual mode), the instant a team
    # founder tries to create their team's message board. See the file's
    # own comment; confirmed present verbatim in upstream at the exact
    # BOINC commit this image builds from.
    docker cp ./html/user/team_forum.php "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/user/team_forum.php"
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
    # Byte-identical copy of BOINC's own file, with one added "Progress"
    # entry in the Computing navbar menu linking to progress.php -- that
    # page had no link anywhere on the site otherwise.
    docker cp ./html/inc/bootstrap.inc "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/inc/bootstrap.inc"
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
    docker cp ./daemon_health_check.sh "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/bin/daemon_health_check.sh"
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
    # Same html/ops/ placement, same reason: run_in_ops ./generate_progress_stats.php
    # (config.xml) only resolves its require_once("../inc/util_ops.inc") one
    # level under html/.
    docker cp ./html/ops/generate_progress_stats.php "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/ops/generate_progress_stats.php"
    # Same html/ops/ placement/reason again -- called by
    # deploy_rollback.sh's --restore path (docker exec, not run_in_ops:
    # it needs a real exit code, not run_in_ops's own error handling) when
    # a deploy published a broken app version and needs to stop it being
    # served.
    docker cp ./html/ops/deprecate_app_version.php "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/ops/deprecate_app_version.php"
    # Same html/ops/ placement/reason again -- byte-identical stock file plus
    # a one-line PHP 8.1 strlen(null) deprecation-notice fix (same class of
    # fix as html/user/get_project_config.php etc.), found live on
    # camicia_ops/login_form.php.
    docker cp ./html/ops/login_form.php "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/ops/login_form.php"
    # Same html/ops/ placement/reason again -- byte-identical stock file plus
    # an uninitialized-$admin fix in handle_suspend() (PHP 8 fatals on
    # property assignment to null instead of PHP <8's silent auto-vivify),
    # found live on camicia_ops/manage_user.php: every single suspend/
    # unsuspend crashed after the DB update committed but before either
    # notification email went out.
    docker cp ./html/ops/manage_user.php "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/html/ops/manage_user.php"

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

    # HMAC secret for cert1.php's verification codes -- optional, only
    # written if CERT_VERIFY_SECRET is set in .env. Same kept-out-of-the-repo,
    # piped-over-stdin treatment as the SMTP credentials just above.
    if [ -n "$CERT_VERIFY_SECRET" ]; then
        docker exec -i "$SERVER_CONTAINER_NAME" bash -c "cat > $PROJECT_DIR/cert_verify_secret.inc.php" <<EOF
<?php
define('CERT_VERIFY_SECRET', '$CERT_VERIFY_SECRET');
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

    CURRENT_STAGE="merging config.xml"
    echo "🐍 Running Python script to update XML nodes..."
    docker exec "$SERVER_CONTAINER_NAME" python3 /tmp/merge_config.py

    CURRENT_STAGE="fixing file permissions"
    echo "🔐 Fixing permissions for user $PROJECTS_USER..."
    # project.xml/db_dump_spec.xml specifically, not a $PROJECT_DIR/*.xml
    # glob: that also matched config.xml (resetting the www-data group
    # fix_permissions.sh had just set on it moments earlier, every single
    # run) and gui_urls.xml/run_state_*.xml, none of which are ever
    # docker-cp'd from outside and so never needed this fix at all.
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown -R $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/assimilator $PROJECT_DIR/worker $PROJECT_DIR/work_generator $PROJECT_DIR/verify_sample $PROJECT_DIR/templates $PROJECT_DIR/project.xml $PROJECT_DIR/db_dump_spec.xml $PROJECT_DIR/html/project/project.inc $PROJECT_DIR/html/project/project_description.php $PROJECT_DIR/html/project/project_specific_prefs.inc $PROJECT_DIR/html/user/signup.php $PROJECT_DIR/html/user/about.php $PROJECT_DIR/html/user/privacy.php $PROJECT_DIR/html/user/progress.php $PROJECT_DIR/html/user/cert1.php $PROJECT_DIR/html/inc/cert.inc $PROJECT_DIR/html/user/verify_cert.php $PROJECT_DIR/html/user/img/camicia_banner.svg $PROJECT_DIR/html/user/img/favicon.svg $PROJECT_DIR/html/user/server_status.php $PROJECT_DIR/html/user/download_network.php $PROJECT_DIR/html/user/get_project_config.php $PROJECT_DIR/html/user/team_members.php $PROJECT_DIR/html/inc/prefs.inc $PROJECT_DIR/html/inc/prefs_project.inc $PROJECT_DIR/html/inc/util.inc $PROJECT_DIR/html/inc/bootstrap.inc $PROJECT_DIR/terms_of_use.txt $PROJECT_DIR/html/inc/PHPMailer $PROJECT_DIR/html/inc/translation.inc $PROJECT_DIR/html/languages/compiled/translation_fixes.inc $PROJECT_DIR/html/ops/login_form.php $PROJECT_DIR/html/ops/manage_user.php 2>/dev/null"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/html/ops/create_forums.php && chmod +x $PROJECT_DIR/html/ops/create_forums.php"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/html/ops/generate_progress_stats.php && chmod +x $PROJECT_DIR/html/ops/generate_progress_stats.php"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/html/ops/deprecate_app_version.php && chmod +x $PROJECT_DIR/html/ops/deprecate_app_version.php"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/bin/db_backup.sh && chmod +x $PROJECT_DIR/bin/db_backup.sh"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/bin/disk_space_check.sh && chmod +x $PROJECT_DIR/bin/disk_space_check.sh"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/bin/memory_check.sh && chmod +x $PROJECT_DIR/bin/memory_check.sh"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/bin/daemon_health_check.sh && chmod +x $PROJECT_DIR/bin/daemon_health_check.sh"
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
    if [ -n "$CERT_VERIFY_SECRET" ]; then
        # Same www-data group-read treatment as smtp_credentials.inc.php --
        # project.inc require_once()s this one too, also running as www-data.
        docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:www-data $PROJECT_DIR/cert_verify_secret.inc.php && chmod 640 $PROJECT_DIR/cert_verify_secret.inc.php"
    fi

    CURRENT_STAGE="compiling and publishing the app version"
    bash ./publish_version.sh

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
    CURRENT_STAGE="restoring upload_private"
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

    CURRENT_STAGE="restarting daemons"
    echo "▶️ Restarting BOINC project..."
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "cd $PROJECT_DIR && ./bin/start"

    docker exec "$SERVER_CONTAINER_NAME" bash -c "rm -f /tmp/config_new.xml /tmp/merge_config.py"

    echo "🚀 Deployment and compilation completed successfully!"
else
    echo "❌ ERROR: Folder $PROJECT_DIR does not exist inside container $SERVER_CONTAINER_NAME."
    echo "   Make sure the container is running and the path is correct."
    exit 1
fi