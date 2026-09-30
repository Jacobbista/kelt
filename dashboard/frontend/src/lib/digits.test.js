import { test } from "node:test";
import assert from "node:assert/strict";
import { isSettled, placeholder, scrambleFrame } from "./digits.js";

const x = () => "x";

test("non-digits never cycle", () => {
  assert.equal(scrambleFrame(null, "12.5", 0, x), "xx.x");
});

test("digits settle left to right, three ticks each", () => {
  assert.equal(scrambleFrame(null, "123", 3, x), "1xx");
  assert.equal(scrambleFrame(null, "123", 6, x), "12x");
  assert.equal(scrambleFrame(null, "123", 9, x), "123");
});

test("a changed value cycles only the digits that changed", () => {
  assert.equal(scrambleFrame("164.2", "168.2", 0, x), "16x.2");
});

test("a value of a different width cycles every digit", () => {
  assert.equal(scrambleFrame("9", "10", 0, x), "xx");
});

test("settled after three ticks per character", () => {
  assert.equal(isSettled("12", 6), false);
  assert.equal(isSettled("12", 9), true);
});

test("placeholder has the requested width", () => {
  assert.equal(placeholder(3, x), "xxx");
});
