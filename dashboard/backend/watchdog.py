#!/usr/bin/env python3
"""Tiny standalone HTTP server (port 31881) that can restart the backend.

Runs as a separate systemd service so it stays alive even when the main
dashboard-backend is hung or crashed. Endpoints:

  GET  /status   — systemctl status + journalctl for dashboard-backend
  POST /restart  — systemctl restart dashboard-backend

Auth model: binds to 127.0.0.1 so only requests proxied through the local
Vite frontend reach it (the Vite proxy in turn is reachable from LAN/tunnel).
Both endpoints require header `X-Watchdog-Token: <WATCHDOG_TOKEN>`; the
token is provisioned via systemd Environment from the same value as
DASHBOARD_ADMIN_TOKEN. The frontend fetches the token from the
authenticated admin router and caches it in memory so it can still
restart the backend after a crash. See docs/security/iam.md.
"""

import http.server
import json
import os
import subprocess

PORT = 31881
BIND = "127.0.0.1"
BACKEND_SERVICE = "dashboard-backend"
WATCHDOG_TOKEN = os.environ.get("WATCHDOG_TOKEN", "")


class WatchdogHandler(http.server.BaseHTTPRequestHandler):
    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if not WATCHDOG_TOKEN:
            return False
        return self.headers.get("X-Watchdog-Token") == WATCHDOG_TOKEN

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
