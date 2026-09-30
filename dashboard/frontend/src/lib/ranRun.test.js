import { test } from "node:test";
import assert from "node:assert/strict";
import { lastRanRun, pathHeadline, runningFrom, stepFor, verifyAfter } from "./ranRun.js";

const link = (id, state, extra = {}) => ({ id, name: { cable: "Cable and link", bridge: "RAN bridge", core: "Core connection", user_plane: "User plane", ues: "UEs" }[id], state, verdict: `${id} ${state}`, fix: null, ...extra });
const chain = (states, first = null) => ({ state: first ? "broken" : "serving", first_broken: first, links: ["cable", "bridge", "core", "user_plane", "ues"].map((id, i) => link(id, states[i])) });

const rec = (piece, state, extra = {}) => ({ id: `20260927-120000-000001-${piece}`, piece, state, started: "2026-09-27T12:00:00Z", steps: [], ...extra });

test("a piece started on this page is the running one", () => {
  assert.deepEqual(runningFrom("ran_link", []), { piece: "ran_link", id: null });
});

test("a RAN piece running from before the page was opened is recognised, with its id", () => {
  const r = rec("ran_attach", "running");
  assert.deepEqual(runningFrom(null, [rec("ran_link", "done"), r]), { piece: "ran_attach", id: r.id });
});

test("other pieces and finished runs block nothing", () => {
  assert.deepEqual(runningFrom(null, [rec("other_piece", "running"), rec("ran_detach", "done")]), { piece: null, id: null });
  assert.deepEqual(runningFrom(null, undefined), { piece: null, id: null });
});

test("progress text is the running record's last step", () => {
  const recs = [rec("ran_attach", "running", { steps: ["Read br-ran", "Wait for br-ran to match"] })];
  assert.equal(stepFor("ran_attach", recs), "Wait for br-ran to match…");
  assert.equal(stepFor("ran_detach", recs), null);
  assert.equal(stepFor("ran_attach", [rec("ran_attach", "running")]), null);
});

test("a fix counts as done only when its link is no longer broken", () => {
  assert.deepEqual(verifyAfter("ran_link", "cable", chain(["ok", "ok", "ok", "ok", "ok"])), { ok: true, message: null });
  const still = chain(["ok", "bad", "blocked", "blocked", "blocked"], "bridge");
  assert.deepEqual(verifyAfter("ran_link", "bridge", still), { ok: false, message: "still broken at RAN bridge: bridge bad" });
});

test("a detach counts as done when the chain reads detached", () => {
  assert.equal(verifyAfter("ran_detach", "bridge", { ...chain(["ok", "idle", "idle", "idle", "idle"]), state: "detached" }).ok, true);
  assert.deepEqual(verifyAfter("ran_detach", "bridge", chain(["ok", "ok", "ok", "ok", "ok"])), { ok: false, message: "the RAN still reads as attached" });
});

test("an attach is not done while the chain still reads detached", () => {
  assert.equal(verifyAfter("ran_attach", "bridge", { ...chain(["ok", "idle", "idle", "idle", "idle"]), state: "detached" }).ok, false);
  assert.equal(verifyAfter("ran_attach", "bridge", chain(["ok", "ok", "bad", "blocked", "blocked"], "core")).ok, true);
});

test("the detached headline", () => {
  const h = pathHeadline({ ...chain(["ok", "idle", "idle", "idle", "idle"]), state: "detached" }, { ues: 0 }, null);
  assert.deepEqual([h.title, h.dot, h.checking], ["Detached", "bg-slate-500", false]);
});

test("no status after the action is not a success", () => {
  assert.equal(verifyAfter("ran_link", "cable", undefined).ok, false);
});

test("headline while the first read is out", () => {
  assert.deepEqual(pathHeadline(undefined, null, null), { title: "Checking the path…", sub: "From the cable to the UEs, in order.", dot: "bg-slate-500", checking: true });
});

test("a failed first read says so and stops checking", () => {
  const h = pathHeadline(undefined, null, "timeout");
  assert.equal(h.title, "Could not read the RAN status");
  assert.equal(h.sub, "timeout");
  assert.equal(h.checking, false);
  assert.equal(h.dot, "bg-rose-400");
});

test("serving and broken headlines", () => {
  assert.equal(pathHeadline(chain(["ok", "ok", "ok", "ok", "ok"]), { ues: 2 }, null).sub, "1 gNB, 2 UEs. Every link from the cable to the UEs checked.");
  const b = pathHeadline(chain(["ok", "bad", "blocked", "blocked", "blocked"], "bridge"), { ues: 0 }, null);
  assert.deepEqual([b.title, b.sub, b.checking], ["Broken at RAN bridge".replace("RAN bridge", "ran bridge"), "bridge bad.", false]);
});

// ── following a piece's operation ──
import { followStart, followStep } from "./ranRun.js";

test("a start without an id, or that failed, ends at once as a failure", () => {
  assert.deepEqual(followStart({ id: null, state: "running" }), { ok: false, message: "the runner did not say which run holds the piece" });
  assert.deepEqual(followStart({ id: "x", state: "failed", error: "could not start: sudo" }), { ok: false, message: "could not start: sudo" });
  assert.equal(followStart({ id: "x", state: "started" }), null);
  assert.equal(followStart({ id: "x", state: "running" }), null);
});

test("a running record keeps waiting and resets the error count", () => {
  assert.deepEqual(followStep({ rec: { state: "running", steps: ["A"] } }, 2), { wait: true, errors: 0 });
});

test("done with a passed or unread check is ok; failed check is not", () => {
  assert.deepEqual(followStep({ rec: { state: "done", check: { ok: true, message: "up" } } }, 0), { done: { ok: true, message: "up", checked: true } });
  assert.deepEqual(followStep({ rec: { state: "done", check: { ok: null, message: "not read back" } } }, 0), { done: { ok: true, message: "not read back", checked: false } });
  assert.deepEqual(followStep({ rec: { state: "failed", exit: 0, check: { ok: false, message: "still down" } } }, 0), { done: { ok: false, message: "still down" } });
});

test("a failed or interrupted run says why", () => {
  assert.deepEqual(followStep({ rec: { state: "failed", exit: 2 } }, 0), { done: { ok: false, message: "the playbook exited 2" } });
  assert.deepEqual(followStep({ rec: { state: "failed", exit: null, error: "FileNotFoundError: x" } }, 0), { done: { ok: false, message: "FileNotFoundError: x" } });
  assert.deepEqual(followStep({ rec: { state: "interrupted" } }, 0), { done: { ok: false, message: "the run was interrupted" } });
});

test("a record that is gone ends the follow", () => {
  assert.deepEqual(followStep({ error: { status: 404 } }, 0), { done: { ok: false, message: "the operation's record is gone" } });
});

test("transient errors are retried, three in a row end it", () => {
  assert.deepEqual(followStep({ error: { status: 502 } }, 0), { wait: true, errors: 1 });
  assert.deepEqual(followStep({ error: new Error("net") }, 1), { wait: true, errors: 2 });
  assert.deepEqual(followStep({ error: { status: 502 } }, 2), { done: { ok: false, message: "lost track of the run (3 failed reads)" } });
});

test("a read-back still settling keeps the page waiting", () => {
  assert.deepEqual(followStep({ rec: { state: "done", exit: 0, check: { ok: null, pending: true, message: "the AMF is still being replaced" } } }, 1), { wait: true, errors: 0 });
});

test("a backend read-back that passed is final; without one the page judges from the chain", () => {
  assert.deepEqual(followStep({ rec: { state: "done", check: { ok: true, message: "attached" } } }, 0), { done: { ok: true, message: "attached", checked: true } });
  assert.deepEqual(followStep({ rec: { state: "done" } }, 0), { done: { ok: true, message: "", checked: false } });
});

test("while a RAN piece runs the headline says what is being done, not broken", () => {
  const broken = chain(["ok", "bad", "blocked", "blocked", "blocked"], "bridge");
  const h = pathHeadline(broken, { ues: 0 }, null, { piece: "ran_attach", step: "Wait for br-ran to match…" });
  assert.deepEqual(h, { title: "Attaching…", sub: "Wait for br-ran to match…", dot: "bg-indigo-400", checking: false });
  assert.equal(pathHeadline(broken, null, null, { piece: "ran_detach", step: null }).title, "Detaching…");
  assert.equal(pathHeadline(broken, null, null, { piece: "ran_link", step: null }).title, "Bringing the link up…");
  assert.equal(pathHeadline(broken, null, null, null).title, "Broken at ran bridge");
});

test("the last RAN result comes from the runner's records", () => {
  const recs = [
    { id: "b", piece: "ran_attach", state: "done", started: "2026-09-27T11:28:34Z", ended: "2026-09-27T11:29:48Z", check: { ok: true } },
    { id: "a", piece: "ran_detach", state: "done", started: "2026-09-27T11:23:31Z", ended: "2026-09-27T11:24:39Z", check: { ok: true } },
    { id: "x", piece: "other", state: "failed", started: "2026-09-27T11:40:00Z", ended: "2026-09-27T11:40:05Z" },
    { id: "r", piece: "ran_link", state: "running", started: "2026-09-27T11:50:00Z" },
  ];
  assert.equal(lastRanRun(recs).id, "b");
  assert.equal(lastRanRun([]), null);
});

test("the step shown while a RAN piece runs is never Ansible's fact gathering", () => {
  const recs = [{ id: "1", piece: "ran_attach", state: "running", steps: ["Read the bridge", "Gathering Facts"] }];
  assert.equal(stepFor("ran_attach", recs), "Read the bridge…");
});
