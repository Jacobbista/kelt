#!/usr/bin/env bash
# Tests for testbed-config's autostart_needrestart_conf: the needrestart
# snippet installed with the autostart unit, loaded as needrestart loads its
# conf.d files, keeps the unit out of automatic restarts and matches nothing
# else. Run: bash tests/cli/autostart-needrestart.test.sh
set -o pipefail

CLI="$(cd "$(dirname "$0")/../.." && pwd)/testbed-config"
eval "$(sed -n '/^autostart_needrestart_conf() {/,/^}/p' "$CLI")"
fails=0
conf="$(mktemp)"
trap 'rm -f "$conf"' EXIT
autostart_needrestart_conf > "$conf"

expect() {  # $1=name $2=wanted (0 = not restarted, none = no rule) $3=service
  local got
  got="$(perl -e '
    our %nrconf = (override_rc => {});
    do $ARGV[0]; die $@ if $@;
    my $v = "none";
    for my $re (keys %{$nrconf{override_rc}}) { $v = $nrconf{override_rc}{$re} if $ARGV[1] =~ $re; }
    print $v;' "$conf" "$3" 2>&1)"
  if [ "$got" = "$2" ]; then echo "ok   $1"; else echo "FAIL $1 (got $got)"; fails=$((fails + 1)); fi
}
expect "the autostart unit is not restarted"   0    5g-testbed.service
expect "a longer unit name has no rule"        none x5g-testbed.service
expect "another unit has no rule"              none ssh.service
exit $fails
