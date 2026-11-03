#!/usr/bin/env bash
# One-time setup: installs dependencies and lets your user
#  - create a virtual keyboard (/dev/uinput)
#  - read the touchscreen directly (needed for multi-touch)
set -euo pipefail

echo "Installing python3-evdev and PyQt6..."
sudo apt install -y python3-evdev python3-pyqt6

echo "Adding udev rule for uinput + touchscreen access..."
sudo tee /etc/udev/rules.d/60-touch-wasd.rules >/dev/null <<'EOF'
KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"
SUBSYSTEM=="input", KERNEL=="event*", ENV{ID_INPUT_TOUCHSCREEN}=="1", TAG+="uaccess"
EOF
sudo rm -f /etc/udev/rules.d/60-touch-wasd-uinput.rules  # old name
sudo udevadm control --reload-rules
sudo udevadm trigger --name-match=uinput || true
sudo udevadm trigger --subsystem-match=input --action=change || true

echo
echo "Done. If ./run.sh complains about permissions, log out and back in."
