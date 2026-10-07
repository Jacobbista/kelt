import { test } from "node:test";
import assert from "node:assert/strict";
import { summarizeStatus } from "./backendStatus.js";

const status = {
  active: false,
  status_output: [
    "○ dashboard-backend.service - 5G Dashboard backend",
    "     Loaded: loaded (/etc/systemd/system/dashboard-backend.service; enabled)",
    "     Active: inactive (dead) since Wed 2026-10-07 15:21:14 UTC; 30s ago",
  ].join("\n"),
  journal: [
    "2026-10-07T15:21:12+0000 ansible sudo[23291]:  vagrant : PWD=/home ; USER=root ; COMMAND=/usr/bin/systemctl is-active dashboard-frontend",
    "2026-10-07T15:21:12+0000 ansible sudo[23291]: pam_unix(sudo:session): session opened for user root(uid=0) by (uid=1000)",
    "2026-10-07T15:21:12+0000 ansible uvicorn[22483]: INFO:     192.168.56.11:8863 - \"GET /api/v1/dev-frontend/status HTTP/1.1\" 200 OK",
    "2026-10-07T15:21:14+0000 ansible uvicorn[22483]: INFO:     Shutting down",
    "2026-10-07T15:21:14+0000 ansible systemd[1]: Stopped 5G Dashboard backend.",
    "2026-10-07T15:21:14+0000 ansible uvicorn[22483]: ERROR:    Something broke",
  ].join("\n"),
};

test("the service state is the Active line, without the unit noise", () => {
  assert.equal(summarizeStatus(status).state, "inactive (dead) since Wed 2026-10-07 15:21:14 UTC; 30s ago");
});

test("the messages leave out sudo, pam and HTTP access lines", () => {
  const msgs = summarizeStatus(status).messages;
  assert.deepEqual(msgs.map((m) => m.text), ["Shutting down", "Stopped 5G Dashboard backend.", "Something broke"]);
});

test("each message keeps its time and level", () => {
  const [first, , last] = summarizeStatus(status).messages;
  assert.equal(first.time, "15:21:14");
  assert.equal(first.level, "info");
  assert.equal(last.level, "error");
});

test("only the last few messages are kept", () => {
  const many = Array.from({ length: 20 }, (_, i) => `2026-10-07T15:21:${String(i).padStart(2, "0")}+0000 ansible uvicorn[1]: INFO:     line ${i}`);
  const msgs = summarizeStatus({ journal: many.join("\n") }).messages;
  assert.equal(msgs.length, 8);
  assert.equal(msgs[7].text, "line 19");
});

test("a watchdog error is passed through as the state", () => {
  assert.deepEqual(summarizeStatus({ error: "Watchdog not reachable" }), { state: null, messages: [], error: "Watchdog not reachable" });
});
