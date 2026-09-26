#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_FRONTEND="${ROOT_DIR}/frontend"
WORK_ROOT="${DASHBOARD_WORK_ROOT:-${HOME}/dashboard-work}"
WORK_FRONTEND="${WORK_ROOT}/frontend"

mkdir -p "${WORK_ROOT}"
rsync -a --delete --exclude "node_modules" "${SOURCE_FRONTEND}/" "${WORK_FRONTEND}/"

cd "${WORK_FRONTEND}"
npm install >/dev/null
npm run dev -- --host 0.0.0.0 --port 5173
