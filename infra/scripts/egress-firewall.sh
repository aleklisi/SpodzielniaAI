#!/usr/bin/env bash
# Prod defence in depth for SSRF (chunk 2.4): drop traffic from the `egress` Docker network to
# private, loopback, link-local (cloud metadata), CGNAT and multicast ranges, including the other
# Docker networks. The application already refuses such targets (net/safe_fetch.py). This rule
# also holds if that code has a bug.
#
# Linux host only, run as root after `docker compose up` (Docker creates DOCKER-USER). Idempotent:
# the rules live in a dedicated chain that is flushed and rebuilt on every run.
#   sudo infra/scripts/egress-firewall.sh            # apply
#   sudo infra/scripts/egress-firewall.sh --dry-run  # print the iptables commands only
#   sudo infra/scripts/egress-firewall.sh --remove   # detach and delete the chain
# Docker Desktop (macOS) runs containers in a VM. There this script is not applicable.
set -euo pipefail

EGRESS_SUBNET="${EGRESS_SUBNET:-172.30.3.0/24}"   # compose.yaml networks.egress
CHAIN="SPOLDZIELNIA-EGRESS"
BLOCKED_V4=(
  0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12
  192.0.0.0/24 192.168.0.0/16 198.18.0.0/15 224.0.0.0/4 240.0.0.0/4
)

DRY_RUN=0
MODE=apply
case "${1:-}" in
  --dry-run) DRY_RUN=1 ;;
  --remove) MODE=remove ;;
  "") ;;
  *) echo "usage: $0 [--dry-run|--remove]" >&2; exit 2 ;;
esac

ipt() {
  if [[ "$DRY_RUN" -eq 1 ]]; then echo "iptables $*"; else iptables "$@"; fi
}

if [[ "$MODE" == remove ]]; then
  ipt -D DOCKER-USER -s "$EGRESS_SUBNET" -j "$CHAIN" 2>/dev/null || true
  ipt -F "$CHAIN" 2>/dev/null || true
  ipt -X "$CHAIN" 2>/dev/null || true
  exit 0
fi

ipt -N "$CHAIN" 2>/dev/null || true
ipt -F "$CHAIN"
# Replies to connections the containers opened (DNS answers from Docker's resolver) stay allowed.
ipt -A "$CHAIN" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
for net in "${BLOCKED_V4[@]}"; do
  ipt -A "$CHAIN" -d "$net" -j DROP
done
ipt -A "$CHAIN" -j RETURN
ipt -C DOCKER-USER -s "$EGRESS_SUBNET" -j "$CHAIN" 2>/dev/null \
  || ipt -I DOCKER-USER 1 -s "$EGRESS_SUBNET" -j "$CHAIN"

# Containers on `egress` have no IPv6 (compose networks are IPv4-only). If IPv6 is enabled
# later, mirror these rules with ip6tables for fc00::/7, fe80::/10, ::1 and 64:ff9b::/96.
echo "egress firewall: ${#BLOCKED_V4[@]} ranges dropped for $EGRESS_SUBNET"
