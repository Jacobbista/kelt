import { test } from "node:test";
import assert from "node:assert/strict";
import { gateView, returnPath } from "./authFlow.js";

const gate = {
  enabled: true, insecureOrigin: false, loading: false, user: {}, loggingOut: false, ended: false,
  path: "/ran", backend: "up", iam: "up", appShown: false,
};

test("the shell opens once there is a session and backend and Keycloak have answered", () => {
  assert.equal(gateView(gate), "app");
  assert.equal(gateView({ ...gate, backend: "unknown" }), "starting");
  assert.equal(gateView({ ...gate, backend: "down" }), "starting");
  assert.equal(gateView({ ...gate, iam: "down" }), "starting");
  assert.equal(gateView({ ...gate, loading: true }), "starting");
});

test("no sign-in redirect while Keycloak is not up", () => {
  assert.equal(gateView({ ...gate, user: null, iam: "down" }), "starting");
});

test("once the shell is open: a lost backend covers it, Keycloak is no longer watched", () => {
  assert.equal(gateView({ ...gate, appShown: true, backend: "down" }), "backend-lost");
  assert.equal(gateView({ ...gate, appShown: true, iam: "down" }), "app");
});

test("an expired perimeter session closes the shell even when it was open", () => {
  assert.equal(gateView({ ...gate, backend: "access-expired", appShown: true }), "access-expired");
  assert.equal(gateView({ ...gate, iam: "access-expired" }), "access-expired");
});

test("no session: first visit redirects, an ended session waits for a click", () => {
  assert.equal(gateView({ ...gate, user: null }), "redirecting");
  assert.equal(gateView({ ...gate, user: null, ended: true }), "ended");
});

test("the auth routes are gate states, never pages inside the shell", () => {
  assert.equal(gateView({ ...gate, user: null, path: "/auth/callback" }), "callback");
  assert.equal(gateView({ ...gate, path: "/auth/callback" }), "callback");
  assert.equal(gateView({ ...gate, user: null, path: "/logged-out" }), "signed-out");
  assert.equal(gateView({ ...gate, loggingOut: true }), "signing-out");
});

test("a signed-in visit to /logged-out opens the shell (App sends it to /)", () => {
  assert.equal(gateView({ ...gate, path: "/logged-out" }), "app");
});

test("plain HTTP stops before anything else", () => {
  assert.equal(gateView({ ...gate, insecureOrigin: true, user: null }), "insecure");
});

test("auth off: only the backend is waited for", () => {
  const off = { ...gate, enabled: false, user: null, iam: "unknown" };
  assert.equal(gateView(off), "app");
  assert.equal(gateView({ ...off, backend: "unknown" }), "starting");
});

test("after a login the user goes back where they were, never to an auth route", () => {
  assert.equal(returnPath("/ran?x=1", null), "/ran?x=1");
  assert.equal(returnPath("/logged-out", "/subscribers"), "/subscribers");
  assert.equal(returnPath("/logged-out", null), "/");
  assert.equal(returnPath("/auth/callback", "/logged-out"), "/");
});
