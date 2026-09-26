#!/usr/bin/env bash
# Tests for .githooks/pre-commit: local-only paths and real SIM keys never reach
# a commit. Runs the hook in a throwaway repo; gitleaks is kept off PATH so only
# the two guards are exercised. Run: bash .githooks/tests/pre-commit.test.sh
set -uo pipefail

HOOK="$(cd "$(dirname "$0")/.." && pwd)/pre-commit"
fails=0

new_repo() {
  local d; d="$(mktemp -d)"
  git -C "$d" init -q
  git -C "$d" config user.email t@example.invalid
  git -C "$d" config user.name test
  echo base > "$d/README"
  # a key that is already public in the tree (like the UERANSIM test key)
  echo "k: 00112233445566778899aabbccddeeff" > "$d/example.yaml"
  git -C "$d" add README example.yaml
  git -C "$d" commit -qm init --no-verify
  printf '%s' "$d"
}

run_hook() {  # $1=repo ; prints the hook's output, returns its exit code
  (cd "$1" && PATH=/usr/bin:/bin bash "$HOOK" 2>&1)
}

expect() {  # $1=name $2=wanted(pass|block) $3=repo
  local out rc
  out="$(run_hook "$3")"; rc=$?
  if { [ "$2" = pass ] && [ $rc -eq 0 ]; } || { [ "$2" = block ] && [ $rc -ne 0 ]; }; then
    echo "ok   $1"
  else
    echo "FAIL $1 (wanted $2, exit $rc)"; echo "$out" | sed 's/^/     /'; fails=$((fails + 1))
  fi
  LAST_OUT="$out"
}

# 1. an ordinary file passes
r="$(new_repo)"; echo hi > "$r/a.txt"; git -C "$r" add a.txt
expect "ordinary file passes" pass "$r"

# 2. local-only paths are refused even when forced past .gitignore
for p in .local/specs/x.md .superpowers/sdd/x/progress.md .claude/skills/x/SKILL.md \
         experiments/runs/r1/raw.csv .testbed.env .testbed.secrets .testbed.subscribers.json; do
  r="$(new_repo)"; mkdir -p "$r/$(dirname "$p")"; echo x > "$r/$p"; git -C "$r" add -f "$p"
  expect "local-only path refused: $p" block "$r"
done

# 3. deleting a local-only path that was committed by mistake is allowed
r="$(new_repo)"; mkdir -p "$r/.local"; echo x > "$r/.local/n.md"
git -C "$r" add -f .local/n.md; git -C "$r" commit -qm oops --no-verify; git -C "$r" rm -q --cached .local/n.md
expect "removing a local-only path passes" pass "$r"

# 4. a real SIM key from the local subscribers file is refused, and not printed
real_k="fedcba98765432100123456789abcdef"; real_opc="0f1e2d3c4b5a69788796a5b4c3d2e1f0"
sub='{"subscribers":[{"imsi":"001010000000001","security":{"k":"'"$real_k"'","opc":"'"$real_opc"'","op":null}}]}'
r="$(new_repo)"; echo "$sub" > "$r/.testbed.subscribers.json"
echo "key: ${real_k^^}" > "$r/notes.md"; git -C "$r" add notes.md
expect "real K refused (any case)" block "$r"
if printf '%s' "$LAST_OUT" | grep -qi "$real_k"; then echo "FAIL the refusal printed the key"; fails=$((fails + 1)); else echo "ok   the refusal does not print the key"; fi

r="$(new_repo)"; echo "$sub" > "$r/.testbed.subscribers.json"
echo "opc $real_opc" > "$r/notes.md"; git -C "$r" add notes.md
expect "real OPc refused" block "$r"

# 5. a key already public in the committed tree is not a leak
pub='{"subscribers":[{"imsi":"001010000000002","security":{"k":"00112233445566778899aabbccddeeff","opc":"'"$real_opc"'"}}]}'
r="$(new_repo)"; echo "$pub" > "$r/.testbed.subscribers.json"
echo "k: 00112233445566778899aabbccddeeff" > "$r/more.yaml"; git -C "$r" add more.yaml
expect "key already in the tree passes" pass "$r"

# 6. no subscribers file: the key guard stays quiet
r="$(new_repo)"; echo "fedcba98765432100123456789abcdef" > "$r/x.txt"; git -C "$r" add x.txt
expect "no subscribers file, nothing to compare" pass "$r"

echo
[ $fails -eq 0 ] && echo "all passed" || { echo "$fails failed"; exit 1; }
