#!/bin/bash
# Docker healthcheck for the server container.
#
# Before the project is bootstrapped (make_project hasn't been run yet, or is
# still running — see README.md §1-2), Apache is up but /camicia/ legitimately
# 500s because html/user doesn't exist yet. That's expected, not a failure, so
# report healthy during that window instead of flapping unhealthy. Once the
# project exists, do a real check against the actual page.
set -eu

PROJECT_DIR="${SERVER_VOLUME_PROJECTS_DIR}/camicia"

if [ ! -d "$PROJECT_DIR/html/user" ]; then
    exit 0
fi

curl -sf -o /dev/null http://localhost/camicia/
