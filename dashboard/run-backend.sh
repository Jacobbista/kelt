#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_BACKEND="${ROOT_DIR}/backend"
WORK_ROOT="${DASHBOARD_WORK_ROOT:-${HOME}/dashboard-work}"
WORK_BACKEND="${WORK_ROOT}/backend"

mkdir -p "${WORK_ROOT}"
rsync -a --delete --exclude "venv" --exclude "__pycache__" "${SOURCE_BACKEND}/" "${WORK_BACKEND}/"

cd "${WORK_BACKEND}"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt >/dev/null
export DASHBOARD_ADMIN_TOKEN="${DASHBOARD_ADMIN_TOKEN:-change-me}"
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
