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

# If anything below fails after daemons are stopped, this leaves the project
# running degraded (down) until camicia.cronjob's 5-minute "bin/start --cron"
# auto-heal happens to notice -- restart immediately instead so a failed
# deploy is loud (you see the actual error) rather than also silently taking
# the project offline for a few minutes.
PROJECT_FOUND=0
recover_daemons_on_failure() {
    local exit_code=$?
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

    echo "🛑 Stopping BOINC project daemons..."
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "cd $PROJECT_DIR && ./bin/stop"

    echo "📂 Copying files and folders to the container..."
    docker cp ./assimilator "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/"
    docker cp ./worker "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/"
    docker cp ./work_generator "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/"
    docker cp ./templates "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/"
    docker cp ./project.xml "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/"
    docker cp ./db_backup.sh "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/bin/db_backup.sh"
    
    echo "🧩 Preparing smart merge of config.xml..."
    docker cp ./config.xml "$SERVER_CONTAINER_NAME":/tmp/config_new.xml
    docker cp ./merge_config.py "$SERVER_CONTAINER_NAME":/tmp/merge_config.py

    echo "🐍 Running Python script to update XML nodes..."
    docker exec "$SERVER_CONTAINER_NAME" python3 /tmp/merge_config.py

    echo "🔐 Fixing permissions for user $PROJECTS_USER..."
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown -R $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/assimilator $PROJECT_DIR/worker $PROJECT_DIR/work_generator $PROJECT_DIR/templates $PROJECT_DIR/*.xml 2>/dev/null"
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/bin/db_backup.sh && chmod +x $PROJECT_DIR/bin/db_backup.sh"

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

    echo "🔄 Registering new app version (update_versions)..."
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c \
        "cd $PROJECT_DIR && ./bin/update_versions --noconfirm"

    echo "▶️ Restarting BOINC project..."
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "cd $PROJECT_DIR && ./bin/start"

    docker exec "$SERVER_CONTAINER_NAME" bash -c "rm -f /tmp/config_new.xml /tmp/merge_config.py"
    
    echo "🚀 Deployment and compilation completed successfully!"
else
    echo "❌ ERROR: Folder $PROJECT_DIR does not exist inside container $SERVER_CONTAINER_NAME."
    echo "   Make sure the container is running and the path is correct."
    exit 1
fi