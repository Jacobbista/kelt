import { test } from "node:test";
import assert from "node:assert/strict";
import { createResourceCache } from "./resourceCache.js";

const tick = () => new Promise((r) => setTimeout(r, 0));

test("empty key reads as nothing loaded", () => {
  const c = createResourceCache();
  assert.deepEqual(c.snapshot("k"), { data: undefined, error: null, updatedAt: null, refreshing: false });
});

test("two refreshes at once make one request", async () => {
  const c = createResourceCache();
  let calls = 0;
  const f = () => { calls++; return Promise.resolve(1); };
  await Promise.all([c.refresh("k", f), c.refresh("k", f)]);
  assert.equal(calls, 1);
  assert.equal(c.snapshot("k").data, 1);
});

test("a failed refresh keeps the last data and records the error", async () => {
  const c = createResourceCache({ now: () => 1000 });
  await c.refresh("k", () => Promise.resolve("good"));
  await c.refresh("k", () => Promise.reject(new Error("boom")));
  const s = c.snapshot("k");
  assert.equal(s.data, "good");
  assert.equal(s.error.message, "boom");
  assert.equal(s.updatedAt, 1000);
});

test("a later success clears the error", async () => {
  const c = createResourceCache();
  await c.refresh("k", () => Promise.reject(new Error("boom")));
  await c.refresh("k", () => Promise.resolve(2));
  assert.equal(c.snapshot("k").error, null);
});

test("refreshing is true while a request is out, and listeners hear both edges", async () => {
  const c = createResourceCache();
  const seen = [];
  c.subscribe("k", () => seen.push(c.snapshot("k").refreshing));
  let release;
  const p = c.refresh("k", () => new Promise((r) => { release = r; }));
  assert.equal(c.snapshot("k").refreshing, true);
  release(3); await p; await tick();
  assert.deepEqual(seen, [true, false]);
});

test("the snapshot object is stable until the entry changes", async () => {
  const c = createResourceCache();
  await c.refresh("k", () => Promise.resolve(1));
  assert.equal(c.snapshot("k"), c.snapshot("k"));
});

test("unsubscribe stops notifications", async () => {
  const c = createResourceCache();
  let n = 0;
  const off = c.subscribe("k", () => n++);
  off();
  await c.refresh("k", () => Promise.resolve(1));
  assert.equal(n, 0);
});

test("after: true waits for the request in flight, then makes a new one", async () => {
  const c = createResourceCache();
  let releaseOld;
  let calls = 0;
  const old = c.refresh("k", () => { calls++; return new Promise((r) => { releaseOld = r; }); });
  const fresh = c.refresh("k", () => { calls++; return Promise.resolve("new"); }, { after: true });
  assert.notEqual(old, fresh);
  releaseOld("old");
  await fresh;
  assert.equal(calls, 2);
  assert.equal(c.snapshot("k").data, "new");
  assert.equal(c.snapshot("k").refreshing, false);
});

test("the older request finishing does not clear the newer one", async () => {
  const c = createResourceCache();
  let releaseOld, releaseNew;
  c.refresh("k", () => new Promise((r) => { releaseOld = r; }));
  const fresh = c.refresh("k", () => new Promise((r) => { releaseNew = r; }), { after: true });
  releaseOld("old");
  await tick(); await tick();
  assert.equal(c.snapshot("k").refreshing, true);
  releaseNew("new"); await fresh;
  assert.equal(c.snapshot("k").refreshing, false);
});
