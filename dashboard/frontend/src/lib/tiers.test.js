import { test } from "node:test";
import assert from "node:assert/strict";
import { triggerClass } from "./tiers.js";

test("a primary trigger takes its tier's colour", () => {
  assert.match(triggerClass("primary", "change"), /indigo/);
  assert.doesNotMatch(triggerClass("primary", "change"), /amber/);
  assert.match(triggerClass("primary", "disrupt"), /amber/);
});

test("a quiet trigger is never all grey for a disrupting action", () => {
  assert.match(triggerClass("quiet", "disrupt"), /text-amber/);
  assert.match(triggerClass("quiet", "change"), /text-indigo/);
});
