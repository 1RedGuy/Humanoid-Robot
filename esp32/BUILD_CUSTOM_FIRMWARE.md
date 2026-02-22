# Custom MicroPython Firmware with 1500-byte UART Buffer

The standard MicroPython ESP32 firmware uses a **260-byte** stdin ring buffer (`stdin_ringbuf_array` in `mphalport.c`). Commands larger than ~256 bytes can overflow and cause Invalid JSON errors.

To increase the buffer to 1500 bytes, build custom firmware:

## Prerequisites

- [ESP-IDF v5.x](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/get-started/) or [esptool](https://github.com/espressif/esptool)
- Git, Python 3, build tools

## Steps

### 1. Clone MicroPython

```bash
git clone https://github.com/micropython/micropython.git
cd micropython
```

### 2. Edit the buffer size

Edit `ports/esp32/mphalport.c` around line 58:

**Before:**
```c
static uint8_t stdin_ringbuf_array[260];
ringbuf_t stdin_ringbuf = {stdin_ringbuf_array, sizeof(stdin_ringbuf_array), 0, 0};
```

**After:**
```c
static uint8_t stdin_ringbuf_array[1500];
ringbuf_t stdin_ringbuf = {stdin_ringbuf_array, sizeof(stdin_ringbuf_array), 0, 0};
```

### 3. Build

```bash
cd ports/esp32
# Set up ESP-IDF (source export.sh or similar)
make submodules
make BOARD=GENERIC
```

### 4. Flash

```bash
# Replace /dev/cu.usbserial-* with your ESP32 port
esptool.py --chip esp32 --port /dev/cu.usbserial-* write_flash 0x1000 build-GENERIC/firmware.bin
```

Or use your existing `flash_firmware.sh` with the new `build-GENERIC/firmware.bin`.

## Alternative: Use a patch

Create `esp32/stdin_ringbuf_1500.patch`:

```diff
--- a/ports/esp32/mphalport.c
+++ b/ports/esp32/mphalport.c
@@ -55,7 +55,7 @@
 
 TaskHandle_t mp_main_task_handle;
 
-static uint8_t stdin_ringbuf_array[260];
+static uint8_t stdin_ringbuf_array[1500];
 ringbuf_t stdin_ringbuf = {stdin_ringbuf_array, sizeof(stdin_ringbuf_array), 0, 0};
```

Apply with: `git apply stdin_ringbuf_1500.patch` (from the micropython repo root).

## Notes

- The change uses ~1.2 KB more static RAM.
- Host-side chunking (in `serial_client.py`) remains useful for reliability.
- After flashing, re-upload `main.py` and other files to the ESP32.
