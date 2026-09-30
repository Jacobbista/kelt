import { test } from "node:test";
import assert from "node:assert/strict";
import { expectedOf, headerSummary, logOpen, progressOf, resultOf, retentionError, stepsShown, took } from "./operations.js";

const rec = (id, state, extra = {}) => ({ id, piece: "ran_link", title: "Bring the RAN link up", state, started: "2026-09-27T10:00:00Z", ...extra });

test("no records at all", () => {
  assert.deepEqual(headerSummary([]), { running: [], more: 0, last: null });
  assert.deepEqual(headerSummary(undefined), { running: [], more: 0, last: null });
});

test("at most three running lines, the rest counted, and the last finished run", () => {
  const recs = [rec("5", "running"), rec("4", "running"), rec("3", "running"), rec("2", "running"), rec("1", "done", { ended: "2026-09-27T10:00:30Z" })];
  const s = headerSummary(recs);
  assert.deepEqual(s.running.map((r) => r.id), ["5", "4", "3"]);
  assert.equal(s.more, 1);
  assert.equal(s.last.id, "1");
});

test("durations", () => {
  const now = Date.parse("2026-09-27T10:00:08Z");
  assert.equal(took(rec("a", "running"), now), "8 s");
  assert.equal(took(rec("a", "done", { ended: "2026-09-27T10:03:10Z" })), "3 min");
  assert.equal(took(rec("a", "done", { ended: "2026-09-27T11:05:00Z" })), "1 h 5 min");
  assert.equal(took({ id: "x", state: "interrupted" }), "—");
});

test("result labels", () => {
  assert.deepEqual(resultOf(rec("a", "running")), { label: "running", tone: "run" });
  assert.deepEqual(resultOf(rec("a", "done", { check: { ok: true } })), { label: "done", tone: "ok" });
  assert.deepEqual(resultOf(rec("a", "done", { check: { ok: null } })), { label: "done, not read back", tone: "ok" });
  assert.deepEqual(resultOf(rec("a", "done", { check: { ok: false } })), { label: "failed", tone: "bad" });
  assert.deepEqual(resultOf(rec("a", "failed", { exit: 2 })), { label: "failed", tone: "bad" });
  assert.deepEqual(resultOf(rec("a", "interrupted")), { label: "interrupted", tone: "bad" });
  assert.deepEqual(resultOf({ id: "a" }), { label: "unknown", tone: "muted" });
  assert.deepEqual(resultOf(rec("a", "done", { reads_back: true })), { label: "exited 0, not read back yet", tone: "muted" });
  assert.deepEqual(resultOf(rec("a", "done", { reads_back: false })), { label: "done", tone: "ok" });
  assert.deepEqual(resultOf(rec("a", "done", { reads_back: true, check: { ok: null, pending: true } })), { label: "exited 0, reading back", tone: "muted" });
});

test("retention limits: whole numbers inside their bounds", () => {
  assert.equal(retentionError("30", "50"), null);
  assert.equal(retentionError("1", "10240"), null);
  assert.equal(retentionError("0", "50"), "Days must be a whole number from 1 to 3650.");
  assert.equal(retentionError("7.5", "50"), "Days must be a whole number from 1 to 3650.");
  assert.equal(retentionError("", "50"), "Days must be a whole number from 1 to 3650.");
  assert.equal(retentionError("30", "10241"), "Size must be a whole number from 1 to 10240 MB.");
  assert.equal(retentionError("30", "x"), "Size must be a whole number from 1 to 10240 MB.");
});

test("expected time from the registry, and progress against it", () => {
  const pieces = { ran_link: { takes_s: 150 } };
  assert.equal(expectedOf(rec("a", "running"), pieces), "~3 min");
  assert.equal(expectedOf(rec("a", "done"), { ran_link: { takes_s: 45 } }), "~45 s");
  assert.equal(expectedOf(rec("a", "done"), {}), null);
  const now = Date.parse("2026-09-27T10:00:40Z");
  assert.equal(progressOf(rec("a", "running"), pieces, now), "40 s of ~3 min");
  assert.equal(progressOf(rec("a", "done", { ended: "2026-09-27T10:01:10Z" }), pieces, now), "1 min");
});

test("long step lists show the last few, and say how many are hidden", () => {
  const steps = Array.from({ length: 12 }, (_, i) => `s${i + 1}`);
  assert.deepEqual(stepsShown(steps, false), { shown: ["s8", "s9", "s10", "s11", "s12"], hidden: 7, first: 8, total: 12 });
  assert.deepEqual(stepsShown(steps, true), { shown: steps, hidden: 0, first: 1, total: 12 });
  assert.deepEqual(stepsShown(["a"], false), { shown: ["a"], hidden: 0, first: 1, total: 1 });
});

test("Ansible's own fact gathering is not a step of the piece", () => {
  const steps = ["Gathering Facts", "Read the bridge", "Gathering Facts", "Restart the AMF"];
  assert.deepEqual(stepsShown(steps, true), { shown: ["Read the bridge", "Restart the AMF"], hidden: 0, first: 1, total: 2 });
});

test("the output opens by itself only for a failed run", () => {
  assert.equal(logOpen(rec("1", "failed")), true);
  assert.equal(logOpen(rec("1", "done", { check: { ok: false } })), true);
  assert.equal(logOpen(rec("1", "done", { check: { ok: true } })), false);
  assert.equal(logOpen(rec("1", "running")), false);
});
