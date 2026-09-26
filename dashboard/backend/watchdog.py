#!/usr/bin/env python3
"""Tiny standalone HTTP server that can restart the backend.

Runs as a separate systemd service so it stays alive even when the main
dashboard-backend is hung or crashed. Endpoints:

  GET  /status   — systemctl status + journalctl for dashboard-backend
  POST /restart  — systemctl restart dashboard-backend

Address and port come from WATCHDOG_BIND / WATCHDOG_PORT (phase 09 sets them to
the ansible VM's management address and all.yml dashboard_watchdog_port), so both
the cluster frontend and the Vite dev frontend can proxy /watchdog to it.
Both endpoints require header `X-Watchdog-Token: <DASHBOARD_ADMIN_TOKEN>`,
compared in constant time; the unit reads the token from a root-owned 0600 file
holding only it (/etc/dashboard-watchdog.env, EnvironmentFile=).
The frontend fetches the token from the authenticated admin router and caches
it in memory so it can still restart the backend after a crash. See docs/security/iam.md.
"""

import hmac
import http.server
import json
import os
import subprocess

PORT = int(os.environ.get("WATCHDOG_PORT", "31881"))
BIND = os.environ.get("WATCHDOG_BIND", "127.0.0.1")
BACKEND_SERVICE = "dashboard-backend"
WATCHDOG_TOKEN = os.environ.get("DASHBOARD_ADMIN_TOKEN", "")


def token_matches(given: str | None, expected: str) -> bool:
    """Constant-time comparison; an unset token refuses every request."""
    if not expected or given is None:
        return False
    return hmac.compare_digest(given.encode(), expected.encode())


class WatchdogHandler(http.server.BaseHTTPRequestHandler):
    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        return token_matches(self.headers.get("X-Watchdog-Token"), WATCHDOG_TOKEN)

    def do_GET(self):
        if self.path == "/status":
            if not self._authorized():
                self._json(401, {"error": "unauthorized"})
                return
            result = {"service": BACKEND_SERVICE}
            try:
                proc = subprocess.run(
                    ["systemctl", "status", BACKEND_SERVICE, "--no-pager", "-l"],
                    capture_output=True, text=True, timeout=5,
                )
                result["status_output"] = proc.stdout.strip()
                result["active"] = proc.returncode == 0
            except Exception as exc:
                result["status_output"] = str(exc)
                result["active"] = False
            try:
                proc = subprocess.run(
                    ["journalctl", "-u", BACKEND_SERVICE, "--no-pager", "-n", "40", "--output=short-iso"],
                    capture_output=True, text=True, timeout=5,
                )
                result["journal"] = proc.stdout.strip()
            except Exception as exc:
                result["journal"] = str(exc)
            self._json(200, result)
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/restart":
            if not self._authorized():
                self._json(401, {"error": "unauthorized"})
                return
            try:
                subprocess.run(
                    ["sudo", "systemctl", "restart", BACKEND_SERVICE],
                    capture_output=True, text=True, timeout=10,
                )
                self._json(200, {"status": "restarting", "service": BACKEND_SERVICE})
            except Exception as exc:
                self._json(500, {"error": str(exc)})
        else:
            self._json(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        pass


def main():
    server = http.server.HTTPServer((BIND, PORT), WatchdogHandler)
    print(f"[watchdog] listening on {BIND}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()


if __name__ == "__main__":
    main()
