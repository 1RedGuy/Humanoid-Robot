Then upload files

**Using ampy:**

```bash
ampy --port /dev/ttyUSB0 put main.py
ampy --port /dev/ttyUSB0 put servo_driver.py
```

**Using mpremote (recommended):**

```bash
mpremote cp main.py :main.py
mpremote cp servo_driver.py :servo_driver.py
```

**You'll do this every time you modify your code!**

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
