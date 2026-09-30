// A word the admin types before a piece that cuts devices off runs
// (pieces.yml confirm_word). Pure (confirm.test.js).
export function wordMatches(typed, word) {
  if (!word) return true;
  return String(typed || "").trim().toLowerCase() === word;
}
