#!/usr/bin/env bash
# Flash MicroPython firmware to the ESP32 (use when you see "invalid header: 0xffffffff").
# Then run deploy.sh to upload Python files.
#
# Usage:
#   bash esp32/flash_firmware.sh [PORT]
#   PORT is optional; e.g. /dev/cu.usbserial-210 . If omitted, we try to detect it.
#
# If the board doesn't enter bootloader: hold BOOT, press EN, release EN, release BOOT,
# then run this script immediately.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FIRMWARE="${PROJECT_ROOT}/ESP32_GENERIC-20251209-v1.27.0.bin"

if [ ! -f "$FIRMWARE" ]; then
  echo "Firmware not found: $FIRMWARE"
  echo "Download from https://micropython.org/download/?port=esp32 (ESP32 generic) and put the .bin in the project root."
  exit 1
fi

PORT="$1"
if [ -z "$PORT" ]; then
  for candidate in /dev/cu.usbserial* /dev/cu.SLAB* /dev/cu.wchusbserial*; do
    if [ -e "$candidate" ]; then
      PORT="$candidate"
      break
    fi
  done
fi
if [ -z "$PORT" ] || [ ! -e "$PORT" ]; then
  echo "No port given or port not found. Usage: $0 [PORT]"
  echo "Example: $0 /dev/cu.usbserial-210"
  exit 1
fi

ESPTOOL=""
for cmd in esptool esptool.py; do command -v $cmd >/dev/null 2>&1 && ESPTOOL="$cmd" && break; done
if [ -z "$ESPTOOL" ]; then
  echo "esptool not found. Install with:  pip install esptool"
  exit 1
fi

echo "==> Port: $PORT"
echo "==> Erasing flash..."
"$ESPTOOL" --chip esp32 --port "$PORT" erase_flash

echo "==> Flashing MicroPython at 0x1000..."
"$ESPTOOL" --chip esp32 --port "$PORT" --baud 460800 write_flash -z 0x1000 "$FIRMWARE"

echo ""
echo "==> Firmware flashed. Unplug and replug the ESP32 (or press EN),"
echo "    wait a few seconds, then run:  bash esp32/deploy.sh"
