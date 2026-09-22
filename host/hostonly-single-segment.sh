#!/usr/bin/env bash
# Pin the VirtualBox host-only adapter to one TCP segment per frame, so the host
# never hands the vbox driver a multi-segment TSO frame the VM then drops (a
# 200 ms retransmit on every host-to-NodePort request larger than one segment).
# Run from the Vagrantfile after up/reload/resume; host-side, idempotent, never
# fatal: only host-originated traffic is affected, so a deploy must not stop on
# it. Argument: the host's address on the private network (the .1 of it).
# Workaround, see docs/known-issues/virtualbox-hostonly-tso.md
set -u
HOST_IP="${1:?usage: hostonly-single-segment.sh <host-ip-on-private-network>}"
IF=$(ip -o -4 addr show | awk -v ip="$HOST_IP/" '$4 ~ "^"ip {print $2; exit}')
[ -n "$IF" ] || { echo "[host-only] no host interface holds $HOST_IP, skipping"; exit 0; }
if ip -d link show "$IF" | grep -Eq 'gso_max_segs 1( |$)'; then
  echo "[host-only] $IF already at one segment per frame"; exit 0
fi
if [ -t 0 ]; then SUDO=sudo; else SUDO="sudo -n"; fi
if $SUDO ip link set dev "$IF" gso_max_segs 1 2>/dev/null && $SUDO ethtool -K "$IF" gso off >/dev/null 2>&1; then
  echo "[host-only] $IF set to one segment per frame (gso_max_segs 1, gso off)"
else
  echo "[host-only] could not set $IF (sudo needed). Run once per boot:"
  echo "            sudo ip link set dev $IF gso_max_segs 1 && sudo ethtool -K $IF gso off"
  echo "            See docs/known-issues/virtualbox-hostonly-tso.md"
fi
exit 0
