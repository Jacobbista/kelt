#!/usr/bin/env bash
# Tests for testbed-config's _piece_confirm: a piece with a confirm_word runs
# only after the word is typed at a terminal, or with --yes. Run:
# bash tests/cli/piece-confirm.test.sh
set -o pipefail

CLI="$(cd "$(dirname "$0")/../.." && pwd)/testbed-config"
eval "$(sed -n '/^_piece_confirm() {/,/^}/p' "$CLI")"
eval "$(sed -n '/^_piece_args() {/,/^}/p' "$CLI")"
fails=0
expect() {  # $1=name $2=wanted rc $3=stdin $4..=args
  local name="$1" want="$2" input="$3"; shift 3
  printf '%s\n' "$input" | _piece_confirm "$@" > /dev/null 2>&1
  local rc=$?
  if [ "$rc" = "$want" ]; then echo "ok   $name"; else echo "FAIL $name (rc $rc, wanted $want)"; fails=$((fails + 1)); fi
}
# args: <word> <yes> <tty>
expect "no word: runs"                    0 ""        ""       no  no
expect "word typed at a terminal: runs"   0 "detach"  detach   no  yes
expect "word typed with spaces: runs"     0 " detach " detach  no  yes
expect "wrong word: refused"              1 "detah"   detach   no  yes
expect "nothing typed: refused"           1 ""        detach   no  yes
expect "no terminal, no --yes: refused"   1 "detach"  detach   no  no
expect "--yes: runs without asking"       0 ""        detach   yes no
# "!" = the word could not be read from the registry: fail closed.
expect "unknown word, no --yes: refused"  1 "detach"  "!"      no  yes
expect "unknown word, --yes: left to the runner" 0 "" "!"      yes no

args() {  # $1=name $2=wanted output ("" = refused) $3..=args
  local name="$1" want="$2"; shift 2
  local out; out="$(_piece_args "$@" 2>/dev/null)"; local rc=$?
  if { [ -z "$want" ] && [ $rc -ne 0 ]; } || { [ -n "$want" ] && [ $rc -eq 0 ] && [ "$out" = "$want" ]; }; then
    echo "ok   $name"; else echo "FAIL $name (rc $rc, out '$out')"; fails=$((fails + 1)); fi
}
args "piece alone"                "ran_detach no"  ran_detach
args "piece and --yes"            "ran_detach yes" ran_detach --yes
args "--yes first"                "ran_detach yes" -y ran_detach
args "a second name is refused"   ""               ran_detach foo
args "an unknown flag is refused" ""               ran_detach --force
args "no piece is refused"        ""               --yes
exit $fails
