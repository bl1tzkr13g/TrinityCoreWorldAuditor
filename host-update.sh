#!/usr/bin/env bash
set -Eeuo pipefail
cd /home/docker_user/TrinityCoreWorldAuditor
REQUEST_DIR=/home/docker_user/TrinityCoreWorldAuditor/update-requests
mkdir -p "$REQUEST_DIR"
# Serialize cron invocations, including slow builds.
exec 9> "$REQUEST_DIR/host-worker.lock"
flock -n 9 || exit 0
if [ -f "$REQUEST_DIR/request" ]; then
  mv "$REQUEST_DIR/request" "$REQUEST_DIR/in-progress"
  update_app() {
    echo "Update started: $(date -Is)"
    runuser -u docker_user -- git pull --ff-only origin main || return 1
    docker compose config --quiet || return 1
    docker compose up -d --build || return 1
    echo "Update completed: $(date -Is)"
  }
  if ! update_app > "$REQUEST_DIR/last-update.log" 2>&1; then
    echo "Update failed: $(date -Is)" >> "$REQUEST_DIR/last-update.log"
  fi
  rm -f "$REQUEST_DIR/in-progress"
fi
# App updates are root-owned operations; publication uses only docker_user's Git
# credentials in an isolated temporary checkout. Publish even with no update request.
if [ -f publish-reports.py ]; then
  mkdir -p "$REQUEST_DIR/reports"
  chown docker_user:docker_user "$REQUEST_DIR/reports"
  runuser -u docker_user -- python3 /home/docker_user/TrinityCoreWorldAuditor/publish-reports.py \
    >> "$REQUEST_DIR/last-publication.log" 2>&1
fi
