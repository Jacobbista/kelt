import { test } from "node:test";
import assert from "node:assert/strict";
import { wordMatches } from "./confirm.js";

test("the typed word must match, ignoring case and outer spaces", () => {
  assert.equal(wordMatches("detach", "detach"), true);
  assert.equal(wordMatches("  Detach ", "detach"), true);
  assert.equal(wordMatches("detac", "detach"), false);
  assert.equal(wordMatches("", "detach"), false);
  assert.equal(wordMatches("detach now", "detach"), false);
});

test("no word asked means nothing to type", () => {
  assert.equal(wordMatches("", null), true);
  assert.equal(wordMatches("", undefined), true);
});
