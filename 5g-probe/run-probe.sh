#!/usr/bin/env bash
# Run the probe as root using the project venv (sudo does not use your activated venv).
# Application code lives in the Python package probe/; this script only invokes it.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT/venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "Missing executable venv: $PY" >&2
  echo "Create it from this directory:" >&2
  echo "  python3 -m venv venv && ./venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
cd "$ROOT"

usage() {
  cat <<'EOF'
Usage: ./run-probe.sh [-h] [-d [on|off]] [-- ARGS_FOR_PYTHON...]

  -h, --help       help
  -d, --debug      tunnel trace on stderr (optional on|off; default on)

Runs sudo ./venv/bin/python -m probe … (root needed for netns/DHCP/tunnels).
EOF
}

PROBE_WEBUI_TUNNEL_DEBUG=""
PY_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -d|--debug)
      shift
      val="on"
      if [[ $# -gt 0 && "$1" != -* ]]; then
        val="$1"
        shift
      fi
      case "${val,,}" in
        on|true|1|yes) PROBE_WEBUI_TUNNEL_DEBUG="1" ;;
        off|false|0|no) PROBE_WEBUI_TUNNEL_DEBUG="0" ;;
        *)
          echo "run-probe.sh: -d expects on|off (got ${val})" >&2
          exit 2
          ;;
      esac
      ;;
    --)
      shift
      PY_ARGS+=("$@")
      break
      ;;
    *)
      PY_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ "$(id -u)" -eq 0 ]]; then
  echo "5g-probe: root." >&2
else
  echo "5g-probe: sudo → Python as root." >&2
  sudo -v || {
    echo "5g-probe: sudo failed." >&2
    exit 1
  }
fi

_GUI_KEEP="DISPLAY,WAYLAND_DISPLAY,XAUTHORITY,XDG_SESSION_TYPE,XDG_SESSION_DESKTOP,XDG_CURRENT_DESKTOP,DBUS_SESSION_BUS_ADDRESS,XDG_RUNTIME_DIR,PATH"

SUDO_PROBE_ENV=()
if [[ -n "${PROBE_WEBUI_TUNNEL_DEBUG}" ]]; then
  SUDO_PROBE_ENV=(env "PROBE_WEBUI_TUNNEL_DEBUG=${PROBE_WEBUI_TUNNEL_DEBUG}")
fi

exec sudo --preserve-env="${_GUI_KEEP}" "${SUDO_PROBE_ENV[@]}" "$PY" -m probe "${PY_ARGS[@]}"
