"""Entry point: python -m probe (normally started via run-probe.sh for sudo + venv)."""

from __future__ import annotations

import os


def main() -> None:
    if os.geteuid() != 0:
        print(
            "WARNING: not running as root — isolate/netns/DHCP/tunnels will fail.\n"
            "         Use: ./run-probe.sh   or   sudo ./venv/bin/python -m probe",
            flush=True,
        )

    from probe.application import (
        _cleanup_stuck_plans,
        _ensure_builtin_plans,
        _fix_results_ownership,
        app,
        restore_webui_tunnels_on_startup,
        socketio,
    )

    _ensure_builtin_plans()
    _cleanup_stuck_plans()
    _fix_results_ownership()
    restore_webui_tunnels_on_startup()
    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=True,
        allow_unsafe_werkzeug=True,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
