#!/bin/bash
# Compiles the C++ apps and publishes a new BOINC app version -- extracted
# from tools.sh's own deploy flow so this exact logic (including the
# code-signing decrypt/re-hide window) exists in exactly one place. Called
# twice in practice: once from tools.sh on every normal deploy, and once
# from deploy_rollback.sh's --restore path when a deploy published a
# broken version and needs to publish a replacement -- see that script's
# own header comment for why a second copy of this logic would be a real
# risk (divergence over time in security-sensitive code), not just
# duplication for its own sake.
#
# Self-contained like deploy_rollback.sh: sources ../.env itself rather
# than relying on the caller's shell state, so it works identically
# whether invoked from tools.sh's normal flow or deploy_rollback.sh's
# recovery flow.
#
# On success, writes the just-published version number to
# $HOME/.camicia_deploy_backups/published_version -- the checkpoint
# deploy_rollback.sh's --restore path reads to decide whether a failed
# deploy needs a replacement version published, not just a file revert.
set -e
cd "$(dirname "$0")"

if [ -f ../.env ]; then
    set -a
    . ../.env
    set +a
else
    echo "❌ ERROR: ../.env file not found!"
    exit 1
fi

PROJECT_DIR="${SERVER_VOLUME_PROJECTS_DIR}/camicia"
KEY_DIR="${SERVER_VOLUME_KEYS_DIR}"
BACKUP_DIR="$HOME/.camicia_deploy_backups"
PUBLISHED_VERSION_FILE="$BACKUP_DIR/published_version"

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
# Akismet key/SMTP credentials elsewhere in tools.sh).
if [ -n "$CODE_SIGN_KEY_PASSPHRASE" ]; then
    echo "🔓 Decrypting code-signing key for this deploy..."
    docker exec -i "$SERVER_CONTAINER_NAME" bash -c \
        "gpg --batch --yes --passphrase-fd 0 -o $KEY_DIR/code_sign_private --decrypt $KEY_DIR/code_sign_private.gpg" \
        <<< "$CODE_SIGN_KEY_PASSPHRASE"
    docker exec "$SERVER_CONTAINER_NAME" bash -c \
        "chown $PROJECTS_USER:$PROJECTS_USER $KEY_DIR/code_sign_private && chmod 600 $KEY_DIR/code_sign_private"
fi

echo "🔄 Registering new app version (update_versions)..."
# Can't trust this command's own exit status: BOINC's update_versions is a
# PHP script that reports a missing/unreadable code-signing key (among
# other fatal conditions) via die("some string"), and PHP's die()/exit()
# with a STRING argument always exits 0 -- confirmed against
# /usr/local/src/boinc/tools/update_versions's own source. Caught live: a
# manual (no CODE_SIGN_KEY_PASSPHRASE) run of this script staged files and
# printed "✅ Published" while update_versions had actually failed and
# registered nothing, and the caller (deploy_rollback.sh) went on to
# deprecate the still-good previous version based on that false success.
# Capture the real output and scan for its own "Error:" marker instead.
UPDATE_VERSIONS_OUTPUT=$(docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c \
    "cd $PROJECT_DIR && ./bin/update_versions --noconfirm" 2>&1)
echo "$UPDATE_VERSIONS_OUTPUT"
if echo "$UPDATE_VERSIONS_OUTPUT" | grep -qi "error:"; then
    echo "❌ update_versions reported an error above -- treating this as a failed publish despite its own exit code."
    exit 1
fi

# The point of no return: a client could fetch this version the instant
# update_versions above returns (it already touches reread_db itself). Only
# past this line does deploy_rollback.sh's --restore need to do more than a
# plain file revert if this run goes on to fail -- see that script's header.
mkdir -p "$BACKUP_DIR"
echo "$NEW_VERSION" > "$PUBLISHED_VERSION_FILE"

echo "🧹 Pruning old app staging directories (apps/$APP_NAME)..."
# Safe: update_versions above already copied this run's files into
# download_dir -- nothing in BOINC's runtime path (scheduler, resend,
# file server) ever reads apps/<app>/<version>/ again afterward, only
# download/ (which this never touches). Keeps the RETAIN_APP_VERSIONS
# most recent version directories (including the one just staged)
# purely as a manual-inspection safety margin, not because anything
# still needs them.
RETAIN_APP_VERSIONS=3
docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "
    cd '$APP_DIR' 2>/dev/null || exit 0
    ls -1 | grep -E '^[0-9]+\.[0-9]+\$' | sort -t. -k1,1n -k2,2n | \
        head -n -$RETAIN_APP_VERSIONS | while read -r OLD_VERSION; do
            echo \"   -> removing apps/$APP_NAME/\$OLD_VERSION\"
            rm -rf \"\$OLD_VERSION\"
        done
"

if [ -n "$CODE_SIGN_KEY_PASSPHRASE" ]; then
    echo "🔒 Re-hiding code-signing key..."
    docker exec "$SERVER_CONTAINER_NAME" bash -c "rm -f $KEY_DIR/code_sign_private"
fi

echo "✅ Published app version $NEW_VERSION"
