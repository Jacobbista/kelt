import { test } from "node:test";
import assert from "node:assert/strict";
import { backendDown, setBackendDown, whenBackendUp, holdWhileDown } from "./backendState.js";

test("the flag follows the last health check", () => {
  setBackendDown(true);
  assert.equal(backendDown(), true);
  setBackendDown(false);
  assert.equal(backendDown(), false);
});

test("whenBackendUp resolves at once when up, and on recovery when down", async () => {
  setBackendDown(false);
  await whenBackendUp();
  setBackendDown(true);
  let done = false;
  const p = whenBackendUp().then(() => { done = true; });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(done, false);
  setBackendDown(false);
  await p;
  assert.equal(done, true);
});

test("reads held while down share one request per key, sent on recovery", async () => {
  setBackendDown(true);
  let sent = 0;
  const send = async () => { sent += 1; return sent; };
  const a = holdWhileDown("/api/x", send);
  const b = holdWhileDown("/api/x", send);
  const c = holdWhileDown("/api/y", send);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(sent, 0);
  setBackendDown(false);
  const [ra, rb] = await Promise.all([a, b, c]);
  assert.equal(ra, rb);
  assert.equal(sent, 2);
});

test("while up, a read is sent at once and not shared", async () => {
  setBackendDown(false);
  let sent = 0;
  const send = async () => (sent += 1);
  await Promise.all([holdWhileDown("/api/x", send), holdWhileDown("/api/x", send)]);
  assert.equal(sent, 2);
});
