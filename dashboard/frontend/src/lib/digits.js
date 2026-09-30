// Frames of the "numbers resolve in place" effect (Foundations spec, 6):
// unknown digits cycle inside the value's own box and settle left to right;
// when a value changes, only the digits that changed cycle. Pure, tested in
// digits.test.js.
const DIGITS = "0123456789";
const TICKS_PER_CHAR = 3;

export const randomDigit = () => DIGITS[Math.floor(Math.random() * 10)];

export function scrambleFrame(prev, final, tick, rnd = randomDigit) {
  const lock = Math.floor(tick / TICKS_PER_CHAR);
  const sameWidth = typeof prev === "string" && prev.length === final.length;
  return [...final]
    .map((c, i) => (!/[0-9]/.test(c) || (sameWidth && prev[i] === c) || i < lock ? c : rnd()))
    .join("");
}

export function isSettled(final, tick) {
  return Math.floor(tick / TICKS_PER_CHAR) > final.length;
}

export function placeholder(width, rnd = randomDigit) {
  return Array.from({ length: Math.max(1, width) }, () => rnd()).join("");
}
