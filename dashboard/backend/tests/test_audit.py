import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from app.services import audit

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
MS = lambda dt: int(dt.timestamp() * 1000)  # noqa: E731


class WriteAuditTest(unittest.TestCase):
    def test_entry_names_who_did_it(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(audit.settings, "audit_log_path", f"{d}/audit.log"):
            token = audit.current_actor.set("jacopo")
            try:
                audit.write_audit("subscriber.create", {"imsi": "001010000000001"})
            finally:
                audit.current_actor.reset(token)
            entry = json.loads(Path(f"{d}/audit.log").read_text().splitlines()[0])
        self.assertEqual(entry["actor"], "jacopo")
        self.assertEqual(entry["event"], "subscriber.create")

    def test_entry_keeps_where_the_request_came_from(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(audit.settings, "audit_log_path", f"{d}/audit.log"):
            token = audit.current_request.set({"ip": "203.0.113.7", "user_agent": "Firefox", "method": "POST", "path": "/api/v1/x"})
            try:
                audit.write_audit("x", {})
            finally:
                audit.current_request.reset(token)
            entry = json.loads(Path(f"{d}/audit.log").read_text())
        self.assertEqual(entry["request"]["ip"], "203.0.113.7")

    def test_no_actor_outside_a_request(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(audit.settings, "audit_log_path", f"{d}/audit.log"):
            audit.write_audit("amf.cni_alert", {})
            entry = json.loads(Path(f"{d}/audit.log").read_text())
        self.assertIsNone(entry["actor"])


class TrimTest(unittest.TestCase):
    def test_entries_older_than_the_retention_are_dropped(self):
        old = json.dumps({"ts": (NOW - timedelta(days=31)).isoformat(), "event": "a"})
        new = json.dumps({"ts": (NOW - timedelta(days=29)).isoformat(), "event": "b"})
        self.assertEqual(audit.trim_lines([old, new], NOW, 30), [new])

    def test_unreadable_lines_are_kept(self):
        self.assertEqual(audit.trim_lines(["not json"], NOW, 30), ["not json"])


class RowsTest(unittest.TestCase):
    def test_keycloak_sign_in(self):
        row = audit.from_user_event(
            {"time": MS(NOW), "type": "LOGIN", "clientId": "dashboard", "userId": "u1", "ipAddress": "10.0.0.5",
             "sessionId": "s1", "details": {"username": "jacopo", "redirect_uri": "https://d/auth/callback",
                                             "auth_type": "code", "code_id": "c1"}}, users={})
        meta = row.pop("meta")
        self.assertEqual(row, {"ts": NOW.isoformat(), "who": "jacopo", "kind": "access", "action": "Signed in",
                               "outcome": "ok", "source": "keycloak", "ip": "10.0.0.5", "detail": "client dashboard"})
        self.assertEqual(meta, {"client": "dashboard", "session": "s1", "redirect uri": "https://d/auth/callback",
                                "auth type": "code"})

    def test_keycloak_failure_names_the_error_and_finds_the_user_by_id(self):
        row = audit.from_user_event(
            {"time": MS(NOW), "type": "REFRESH_TOKEN_ERROR", "clientId": "dashboard", "userId": "u1",
             "error": "invalid_token", "details": {"reason": "Session not active", "refresh_token_id": "r1"}},
            users={"u1": "jacopo"})
        self.assertEqual(row["meta"]["reason"], "Session not active")
        self.assertNotIn("refresh token id", row["meta"])
        self.assertEqual((row["who"], row["action"], row["outcome"]), ("jacopo", "Session renewal failed", "failed"))
        self.assertEqual(row["detail"], "client dashboard · invalid_token")

    def test_a_failed_renewal_names_the_user_from_the_token_subject(self):
        row = audit.from_user_event(
            {"time": MS(NOW), "type": "REFRESH_TOKEN_ERROR", "clientId": "dashboard", "error": "invalid_token",
             "details": {"refresh_token_sub": "u1"}}, users={"u1": "admin"})
        self.assertEqual(row["who"], "admin")

    def test_keycloak_admin_event(self):
        row = audit.from_admin_event(
            {"time": MS(NOW), "operationType": "UPDATE", "resourceType": "CLIENT",
             "resourcePath": "clients/abc", "authDetails": {"userId": "m1", "ipAddress": "10.0.0.9"}},
            users={"m1": "admin"})
        self.assertEqual((row["who"], row["kind"], row["action"], row["outcome"], row["ip"]),
                         ("admin", "change", "Update client", "ok", "10.0.0.9"))
        self.assertEqual(row["detail"], "clients/abc")

    def test_dashboard_action(self):
        row = audit.from_local({"ts": NOW.isoformat(), "event": "subscriber.delete", "actor": "jacopo",
                                "details": {"imsi": "001010000000001"}})
        self.assertEqual((row["who"], row["kind"], row["action"], row["source"]),
                         ("jacopo", "change", "subscriber.delete", "dashboard"))
        self.assertEqual(row["detail"], "imsi=001010000000001")

    def test_dashboard_action_shows_where_it_came_from(self):
        row = audit.from_local({"ts": NOW.isoformat(), "event": "x", "actor": "a", "details": {"imsi": "1"},
                                "request": {"ip": "203.0.113.7", "user_agent": "Firefox", "method": "POST", "path": "/p"}})
        self.assertEqual(row["ip"], "203.0.113.7")
        self.assertEqual(row["meta"], {"imsi": "1", "request": "POST /p", "browser": "Firefox"})

    def test_dashboard_action_from_before_actors_were_recorded(self):
        self.assertEqual(audit.from_local({"ts": NOW.isoformat(), "event": "x", "details": {}})["who"], "unknown")


class MergeTest(unittest.TestCase):
    ROWS = [
        {"ts": (NOW - timedelta(hours=3)).isoformat(), "kind": "access", "outcome": "ok"},
        {"ts": (NOW - timedelta(hours=1)).isoformat(), "kind": "change", "outcome": "ok"},
        {"ts": (NOW - timedelta(hours=2)).isoformat(), "kind": "access", "outcome": "failed"},
        {"ts": (NOW - timedelta(days=9)).isoformat(), "kind": "change", "outcome": "ok"},
    ]

    def test_newest_first_within_the_period(self):
        out = audit.select(self.ROWS, "all", NOW - timedelta(days=7))
        self.assertEqual([r["ts"] for r in out], sorted([r["ts"] for r in self.ROWS[:3]], reverse=True))

    def test_filters(self):
        since = NOW - timedelta(days=30)
        self.assertEqual(len(audit.select(self.ROWS, "access", since)), 2)
        self.assertEqual(len(audit.select(self.ROWS, "changes", since)), 2)
        self.assertEqual([r["outcome"] for r in audit.select(self.ROWS, "failures", since)], ["failed"])

    def test_limit(self):
        self.assertEqual(len(audit.select(self.ROWS, "all", NOW - timedelta(days=30), limit=2)), 2)



class ActorMiddlewareTest(unittest.TestCase):
    """The route sees who called it, also when it is a plain (threadpool) def."""

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from jose import jwt as jose_jwt

        from app.auth import ActorMiddleware

        app = FastAPI()
        app.add_middleware(ActorMiddleware)
        app.get("/who")(lambda: {"actor": audit.current_actor.get(), "request": audit.current_request.get()})
        token = jose_jwt.encode({"preferred_username": "jacopo"}, "k", algorithm="HS256")
        return TestClient(app), token

    def test_actor_from_the_bearer_token(self):
        client, token = self._client()
        with mock.patch.object(audit.settings, "skip_auth", False):
            self.assertEqual(client.get("/who", headers={"Authorization": f"Bearer {token}"}).json()["actor"], "jacopo")

    def test_no_token_no_actor(self):
        client, _ = self._client()
        with mock.patch.object(audit.settings, "skip_auth", False):
            self.assertEqual(client.get("/who").json()["actor"], None)

    def test_request_origin_prefers_the_forwarded_client(self):
        client, _ = self._client()
        got = client.get("/who", headers={"CF-Connecting-IP": "203.0.113.7", "X-Forwarded-For": "198.51.100.1, 10.0.0.1",
                                          "User-Agent": "Firefox"}).json()["request"]
        self.assertEqual((got["ip"], got["user_agent"], got["method"], got["path"]), ("203.0.113.7", "Firefox", "GET", "/who"))
        got = client.get("/who", headers={"X-Forwarded-For": "198.51.100.1, 10.0.0.1"}).json()["request"]
        self.assertEqual(got["ip"], "198.51.100.1")

if __name__ == "__main__":
    unittest.main()
