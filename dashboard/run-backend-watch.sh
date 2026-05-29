#!/usr/bin/env bash
# Wrapper that restarts the backend on crash (for manual runs).
# With systemd (Ansible deploy) Restart=always is already configured.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_BACKEND="${ROOT_DIR}/backend"
WORK_ROOT="${DASHBOARD_WORK_ROOT:-${HOME}/dashboard-work}"
WORK_BACKEND="${WORK_ROOT}/backend"
PORT="${DASHBOARD_PORT:-8080}"

mkdir -p "${WORK_ROOT}"
rsync -a --delete --exclude "venv" --exclude "__pycache__" "${SOURCE_BACKEND}/" "${WORK_BACKEND}/"

cd "${WORK_BACKEND}"
python3 -m venv venv 2>/dev/null || true
source venv/bin/activate
pip install -r requirements.txt -q
export DASHBOARD_ADMIN_TOKEN="${DASHBOARD_ADMIN_TOKEN:-change-me}"

echo "Backend watch: hot-reload on source changes (Ctrl+C to exit)"
uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" \
  --reload --reload-dir "${SOURCE_BACKEND}/app" \
  --reload-include "*.py"
