import { test } from "node:test";
import assert from "node:assert/strict";
import { autoLogin } from "./authFlow.js";

const base = { enabled: true, loading: false, user: null, loggingOut: false, ended: false, path: "/ran" };

test("a first visit with no session goes to the login page by itself", () => {
  assert.equal(autoLogin(base), true);
});

test("a session that ended while the page was open waits for a click", () => {
  assert.equal(autoLogin({ ...base, ended: true }), false);
});

test("no redirect while signed in, loading, signing out, or on the auth pages", () => {
  assert.equal(autoLogin({ ...base, user: {} }), false);
  assert.equal(autoLogin({ ...base, loading: true }), false);
  assert.equal(autoLogin({ ...base, loggingOut: true }), false);
  assert.equal(autoLogin({ ...base, path: "/auth/callback" }), false);
  assert.equal(autoLogin({ ...base, path: "/logged-out" }), false);
  assert.equal(autoLogin({ ...base, enabled: false }), false);
});
