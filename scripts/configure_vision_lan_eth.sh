#!/usr/bin/env bash
# Vision Pi: static IPv4 on wired Ethernet (Option A — same LAN as master 192.168.10.1).
# Requires cable linked to the master's switch (eth0 carrier UP).
set -euo pipefail

VISION_IP="${VISION_LAN_IP:-192.168.10.2}"
PREFIX="${VISION_LAN_PREFIX:-24}"
GATEWAY="${VISION_LAN_GATEWAY:-192.168.10.1}"
DNS="${VISION_LAN_DNS:-192.168.10.1}"
CONN="${VISION_NM_CONN:-Wired connection 1}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run with sudo: sudo $0" >&2
  exit 1
fi

if ! command -v nmcli >/dev/null 2>&1; then
  echo "nmcli not found (NetworkManager required)" >&2
  exit 1
fi

echo "Connection: $CONN"
echo "Setting ${VISION_IP}/${PREFIX} gateway ${GATEWAY} dns ${DNS}"
nmcli connection modify "$CONN" \
  ipv4.method manual \
  ipv4.addresses "${VISION_IP}/${PREFIX}" \
  ipv4.gateway "$GATEWAY" \
  ipv4.dns "$DNS" \
  ipv6.method ignore
nmcli connection up "$CONN"

echo ""
echo "Verify:"
ip -4 addr show eth0 2>/dev/null || ip -4 addr show
echo ""
ping -c 2 "$GATEWAY" || true
echo ""
echo "On master: ping -c 2 ${VISION_IP}"
echo "  curl -s -H \"X-Vision-Remote-Key: \$VISION_REMOTE_KEY\" http://${VISION_IP}:5000/api/remote/info"
