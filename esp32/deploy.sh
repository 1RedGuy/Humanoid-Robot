#!/usr/bin/env bash
# Deploy all ESP32 files via mpremote.
# Requires MicroPython already on the device. If you see "invalid header: 0xffffffff",
# run:  bash esp32/flash_firmware.sh   first, then unplug/replug and run this.
#
# Usage: Stop the Python backend (and Thonny) first, then:
#   bash esp32/deploy.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Connecting to ESP32 (waiting for REPL)..."
if ! mpremote connect auto + exec "
import os
try: os.mkdir('lib')
except: pass
try: os.mkdir('lib/pca9685')
except: pass
print('Directories ready')
"; then
  echo ""
  echo "Connection failed. If you see 'invalid header: 0xffffffff', the ESP32 has no firmware."
  echo "Run:  bash esp32/flash_firmware.sh"
  echo "Then unplug/replug the ESP32 and run this script again."
  exit 1
fi

echo "==> Uploading files..."
mpremote connect auto \
    + fs cp "$SCRIPT_DIR/lib/pca9685/pca9685.py" :lib/pca9685/pca9685.py \
    + fs cp "$SCRIPT_DIR/servo_driver.py" :servo_driver.py \
    + fs cp "$SCRIPT_DIR/main.py" :main.py \
    + fs cp "$SCRIPT_DIR/servo_data.json" :servo_data.json \
    + reset

echo ""
echo "==> Done! All files uploaded and ESP32 reset."
echo "    Wait a few seconds for calibration, then start the backend."
