import { test } from "node:test";
import assert from "node:assert/strict";
import { shortWhen } from "./when.js";

const now = new Date(2026, 9, 7, 18, 0);

test("today: the time only", () => {
  assert.equal(shortWhen(new Date(2026, 9, 7, 13, 29).toISOString(), now), "13:29");
});

test("yesterday: says so", () => {
  assert.equal(shortWhen(new Date(2026, 9, 6, 23, 5).toISOString(), now), "yesterday 23:05");
});

test("earlier: day and month", () => {
  assert.match(shortWhen(new Date(2026, 9, 2, 9, 0).toISOString(), now), /^2 Oct 09:00$/);
});

test("unreadable: empty", () => {
  assert.equal(shortWhen("x", now), "");
});
