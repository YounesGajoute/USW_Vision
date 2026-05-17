#!/bin/bash
# Install systemd unit so Vision Inspection starts at boot (npm run start:all).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SRC="$ROOT/scripts/inspection-vision.service"
UNIT_DST="/etc/systemd/system/inspection-vision.service"

if [[ ! -f "$UNIT_SRC" ]]; then
  echo "Missing $UNIT_SRC" >&2
  exit 1
fi

echo "Installing $UNIT_DST from repo..."
sudo cp "$UNIT_SRC" "$UNIT_DST"
sudo systemctl daemon-reload
sudo systemctl enable --now inspection-vision.service

echo "Enabled at boot and started now. After reboot it comes up without manual curl/ss checks."
echo "  sudo systemctl status inspection-vision"
echo "  sudo journalctl -u inspection-vision -f"
