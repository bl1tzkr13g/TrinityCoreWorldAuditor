#!/usr/bin/env bash
set -Eeuo pipefail
cd /home/docker_user/TrinityCoreWorldAuditor
REQUEST_DIR=/home/docker_user/TrinityCoreWorldAuditor/update-requests
mkdir -p "$REQUEST_DIR"
if [ ! -f "$REQUEST_DIR/request" ]; then exit 0; fi
mv "$REQUEST_DIR/request" "$REQUEST_DIR/in-progress"
{
  echo "Update started: $(date -Is)"
  git pull --ff-only origin main
  docker compose up -d --build
  echo "Update completed: $(date -Is)"
} > "$REQUEST_DIR/last-update.log" 2>&1 || {
  echo "Update failed: $(date -Is)" >> "$REQUEST_DIR/last-update.log"
  rm -f "$REQUEST_DIR/in-progress"
  exit 1
}
rm -f "$REQUEST_DIR/in-progress"
