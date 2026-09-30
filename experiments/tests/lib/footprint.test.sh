#!/usr/bin/env bash
# Tests for footprint_start / footprint_stop (lib/common.sh) on this host, the
# VMs left out and kubectl stubbed. Run: bash experiments/tests/lib/footprint.test.sh
set -o pipefail
fails=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fails=$((fails + 1)); }
expect() { if eval "$2"; then ok "$1"; else bad "$1: $3"; fi; }

D="$(mktemp -d)"
out="$(bash -c "
  source '$(cd "$(dirname "$0")/../../lib" && pwd)/common.sh'
  FOOTPRINT_VMS=''; FOOTPRINT_TMP='$D'
  kubectl() { printf 'u-1\t5g\tupf-1\n'; }
  footprint_start t1 30 '$D/fp'
  sleep 2.5
  footprint_stop t1 '$D/fp'
" 2>&1)"
expect "start and stop run under set -u" "[ -z \"\$out\" ]" "$out"
expect "the host's samples are kept, gzipped" "zcat '$D/fp/host.txt.gz' | grep -q '^N '" "$(ls "$D/fp" 2>&1)"
expect "no pod lines from the host" "! zcat '$D/fp/host.txt.gz' | grep -q '^P '" ""
expect "the pod map, taken at start and stop, once" "[ \"\$(wc -l < '$D/fp/pods.tsv')\" = 1 ]" "$(cat "$D/fp/pods.tsv" 2>&1)"
expect "the sampler is stopped and its files removed" "! ls '$D'/kelt-fp-t1-host.* >/dev/null 2>&1" "$(ls "$D")"
rm -rf "$D"
# A VM, simulated by running its command in a local shell as vagrant ssh -c
# would: footprint_start must return at once, the sampler left running apart.
D="$(mktemp -d)"; tag="t2-$$"
out="$(bash -c "
  source '$(cd "$(dirname "$0")/../../lib" && pwd)/common.sh'
  FOOTPRINT_VMS='master'; FOOTPRINT_TMP='$D'
  kubectl() { printf 'u-1\t5g\tupf-1\n'; }
  vm_sh() { bash -c \"\$2\" | cat; }
  s=\$SECONDS; footprint_start $tag 30 '$D/fp'; echo started_in=\$(( SECONDS - s ))
  sleep 2.5
  footprint_stop $tag '$D/fp'
" 2>&1)"
expect "a VM's sampler starts in the background: start returns at once" "grep -qE '^started_in=[0-4]$' <<< \"\$out\"" "$out"
expect "the VM's samples are fetched" "zcat '$D/fp/master.txt.gz' | grep -q '^N '" "$(ls "$D/fp" 2>&1)"
rm -rf "$D" /tmp/kelt-fp-$tag.*

exit $fails
