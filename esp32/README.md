Then upload the Python files and config. **Stop the manual_debug server first** (it uses the serial port).

**Option A – Thonny (recommended if mpremote fails)**  
1. Install [Thonny](https://thonny.org/) and open it.  
2. Run → Configure interpreter → Interpreter: MicroPython (ESP32), Port: `/dev/cu.usbserial-210` (or your port).  
3. Connect the ESP32, press Stop (red square) so the device is at the REPL.  
4. In Thonny: File → Open → open `esp32/main.py` from your project.  
5. File → Save as… → **MicroPython device** → save as `main.py`.  
6. Repeat for `servo_driver.py` and `servo_data.json` (save to device with the same names).  
7. Use Run → Restart backend to run the new code.

**Option B – mpremote**  
Stop the backend, then run `bash esp32/deploy.sh` (see Scripted deploy below).

**Option C – ampy (Mac/Linux, one file per command)**  
Backend must be stopped. Port on Mac is often `/dev/cu.usbserial-210`.

```bash
ampy -p /dev/cu.usbserial-210 put esp32/main.py main.py
ampy -p /dev/cu.usbserial-210 put esp32/servo_driver.py servo_driver.py
ampy -p /dev/cu.usbserial-210 put esp32/servo_data.json servo_data.json
```

**You need to upload again after every code change.**

**Scripted deploy**  
Stop the backend and Thonny first. If you see **"invalid header: 0xffffffff"**, the ESP32 has no firmware — flash it once:
```bash
pip install esptool   # if needed
bash esp32/flash_firmware.sh
```
Then unplug/replug the ESP32, wait a few seconds, and run:
```bash
bash esp32/deploy.sh
```
That uploads the PCA9685 library, main.py, servo_driver.py, and servo_data.json. For code-only changes, just run `bash esp32/deploy.sh` (no flash needed).

**Cheeks / eyebrows (or other servos) don’t go to neutral on start**  
On power-up the ESP32 applies **neutral** from `servo_data.json` (every servo listed in `expressions.neutral`). If you added servos to neutral (e.g. cheeks, eyebrows) but didn’t redeploy, the ESP32 still has the old file. Run `bash esp32/deploy.sh` so the device gets the updated `servo_data.json`; after the next reset, all servos in neutral will move to their neutral pose.

**Servos don't move**  
1. In the manual_debug UI, click Connect — it must show "Connected (port)". When you move a slider, the backend terminal should show e.g. `[set-angles] sending 2 servos: ...`. If you never see that, the backend isn't talking to the ESP32 (wrong port or not connected).  
2. To see ESP32-side errors: stop the backend, open Thonny, connect to the ESP32, press Reset. In the Thonny shell you should see either **"ServoDriver init failed: ..."** (fix: missing `lib/pca9685/pca9685.py`, missing `servo_data.json`, or I2C/pins) or **"Loaded servo config"** and **"ESP32 Servo Controller Ready"**.  
3. I2C pins in `servo_driver.py` are 21 (SDA) and 22 (SCL). If your board uses different pins, change them.

**Recovery – "Device is busy" / can't get REPL**  
If Thonny says the device is busy and Stop doesn’t work, the board is running `main.py` and holding the serial port. You can recover by doing a **factory reset** (erase flash, reflash MicroPython, then upload your files again with Thonny):

1. **Close Thonny** and stop any app using the serial port (e.g. manual_debug).
2. **Erase flash** (use your port, e.g. `/dev/cu.usbserial-210`):
   ```bash
   pip install esptool
   esptool.py -p /dev/cu.usbserial-210 erase_flash
   ```
   If it fails, put the ESP32 in bootloader mode: hold **BOOT**, press **EN** (reset), release **EN**, then release **BOOT**, and run the command again.
3. **Download MicroPython** for ESP32: https://micropython.org/download/?port=esp32 (e.g. **ESP32 generic**).
4. **Flash the firmware** (replace `firmware.bin` with the file you downloaded):
   ```bash
   esptool.py -p /dev/cu.usbserial-210 write_flash -z 0x1000 firmware.bin
   ```
   (If the download page gives a different offset, use that.)
5. **Unplug and replug** the ESP32. It should boot to a clean REPL (no `main.py`).
6. **Open Thonny**, set interpreter to MicroPython (ESP32) and the correct port. You should see the REPL prompt.
7. **Upload your files**: Save `esp32/main.py` → device as `main.py`, then `servo_driver.py`, `servo_data.json`. Create folder `lib` on the device, then `lib/pca9685`, and upload `esp32/lib/pca9685/pca9685.py` into it.

After that, Run (or reset the board) and your code will run.

### 4. Hardware Connections

- ESP32 SCL → PCA9685 SCL
- ESP32 SDA → PCA9685 SDA
- ESP32 GND → PCA9685 GND
- ESP32 5V → PCA9685 VCC (or use external 5V power)

**Note:** Adjust I2C pins in `servo_driver.py` if your ESP32 uses different pins.

### 5. Communication

The ESP32 communicates with the Mac via USB serial at 115200 baud. Commands are sent as JSON strings, one per line.

## Command Format

Commands are sent as JSON over serial, ending with newline:

```json
{"command": "move_servo", "servo_id": 1, "angle": 90, "duration": 0.5}
{"command": "move_multiple_servos", "servos": [{"servo_id": 1, "angle": 90, "duration": 0.5}]}
{"command": "calibrate_servos"}
{"command": "stop"}
```
