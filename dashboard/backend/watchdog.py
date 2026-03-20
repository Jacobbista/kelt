#!/usr/bin/env python3
"""Tiny standalone HTTP server (port 31881) that can restart the backend.

Runs as a separate systemd service so it stays alive even when the main
dashboard-backend is hung or crashed.  Endpoints:

  GET  /status   — systemctl status + journalctl for dashboard-backend
  POST /restart  — systemctl restart dashboard-backend
"""

import http.server
import json
import subprocess
import sys

PORT = 31881
BACKEND_SERVICE = "dashboard-backend"


class WatchdogHandler(http.server.BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/status":
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
        # Quiet unless error
        pass


def main():
    server = http.server.HTTPServer(("0.0.0.0", PORT), WatchdogHandler)
    print(f"[watchdog] listening on :{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()


if __name__ == "__main__":
    main()
