#!/bin/bash
set -e

if [ -f ../.env ]; then
    set -a
    . ../.env
    set +a
else
    echo "❌ ERROR: ../.env file not found! Make sure to run this script from the right directory."
    exit 1
fi

PROJECT_DIR="${SERVER_VOLUME_PROJECTS_DIR}/camicia"

echo "🔍 Checking if project exists in the container..."
if docker exec "$SERVER_CONTAINER_NAME" bash -c "[ -d \"$PROJECT_DIR\" ]"; then
    echo "✅ Project found! Starting deployment..."

    echo "🛑 Stopping BOINC project daemons..."
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "cd $PROJECT_DIR && ./bin/stop"

    echo "📂 Copying files and folders to the container..."
    docker cp ./assimilator "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/"
    docker cp ./worker "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/"
    docker cp ./templates "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/"
    docker cp ./project.xml "$SERVER_CONTAINER_NAME":"$PROJECT_DIR/"
    
    echo "🧩 Preparing smart merge of config.xml..."
    docker cp ./config.xml "$SERVER_CONTAINER_NAME":/tmp/config_new.xml
    docker cp ./merge_config.py "$SERVER_CONTAINER_NAME":/tmp/merge_config.py

    echo "🐍 Running Python script to update XML nodes..."
    docker exec "$SERVER_CONTAINER_NAME" python3 /tmp/merge_config.py

    echo "🔐 Fixing permissions for user $PROJECTS_USER..."
    docker exec "$SERVER_CONTAINER_NAME" bash -c "chown -R $PROJECTS_USER:$PROJECTS_USER $PROJECT_DIR/assimilator $PROJECT_DIR/worker $PROJECT_DIR/templates $PROJECT_DIR/*.xml 2>/dev/null"

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

    echo "🔄 Applying configuration changes (xadd)..."
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "cd $PROJECT_DIR && ./bin/xadd"

    echo "▶️ Restarting BOINC project..."
    docker exec --user "$PROJECTS_USER" "$SERVER_CONTAINER_NAME" bash -c "cd $PROJECT_DIR && ./bin/start"

    docker exec "$SERVER_CONTAINER_NAME" bash -c "rm -f /tmp/config_new.xml /tmp/merge_config.py"
    
    echo "🚀 Deployment and compilation completed successfully!"
else
    echo "❌ ERROR: Folder $PROJECT_DIR does not exist inside container $SERVER_CONTAINER_NAME."
    echo "   Make sure the container is running and the path is correct."
    exit 1
fi