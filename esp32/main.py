"""
ESP32 Main Program - Serial Command Receiver
Receives servo commands from Mac via USB serial and executes them on PCA9685

On MicroPython v1.27+ for ESP32, UART(0) is the REPL and cannot be
re-initialised via machine.UART().  We read from sys.stdin instead,
using select.poll() for non-blocking checks.
"""
import json
import sys
import select

_poll = select.poll()
_poll.register(sys.stdin, select.POLLIN)

servo_driver = None
try:
    from servo_driver import ServoDriver
    servo_driver = ServoDriver()
except Exception as e:
    print("ServoDriver init failed:", e)

_MOVE_COMMANDS = ("move_servo", "move_multiple_servos", "set_angles")


def process_command(command_data):
    """Process incoming JSON command and execute servo movement."""
    if servo_driver is None:
        print("No driver (init failed), ignoring command")
        return
    try:
        command = command_data.get("command")

        if command == "move_servo":
            servo_id = command_data["servo_id"]
            angle = command_data["angle"]
            duration = command_data.get("duration", 0.5)
            servo_driver.move_servo(servo_id, angle, duration)

        elif command == "set_angles":
            n = len(command_data.get("servos", []))
            print("set_angles", n, "servos")
            servo_driver.set_angles(command_data["servos"])

        elif command == "move_multiple_servos":
            servos = command_data["servos"]
            duration = 0.5
            if servos:
                duration = servos[0].get("duration", 0.5)
            servo_driver.move_multiple_servos(servos, duration=duration)

        elif command == "calibrate_servos":
            servo_driver.calibrate_servos()

        elif command == "stop":
            servo_driver.stop_all()

        else:
            print(f"Unknown command: {command}")

    except Exception as e:
        print(f"Error processing command: {e}")


def _parse_json_line(line):
    """Parse one or more JSON objects from a line (handles concatenated objects with no newline)."""
    line = line.strip()
    if not line:
        return []
    try:
        return [json.loads(line)]
    except Exception:
        pass
    out = []
    rest = line
    while rest:
        try:
            obj = json.loads(rest)
            out.append(obj)
            break
        except Exception:
            idx = rest.find("}{", 1)
            if idx < 0:
                print("Invalid JSON:", rest[:80], "..." if len(rest) > 80 else "")
                break
            try:
                out.append(json.loads(rest[: idx + 1]))
            except Exception:
                print("Invalid JSON (first part):", rest[: min(80, idx + 1)], "...")
                break
            rest = rest[idx + 1 :].strip()
    return out


def _drain_buffer(buffer):
    """Parse all complete JSON lines out of *buffer*.

    Returns (commands_list, remaining_buffer).
    Handles lines that contain multiple concatenated JSON objects (no newline between them).
    """
    commands = []
    while '\n' in buffer:
        line, buffer = buffer.split('\n', 1)
        commands.extend(_parse_json_line(line))
    return commands, buffer


def main():
    """Main loop - continuously read from serial and process commands."""
    print("ESP32 Servo Controller Ready")
    print("Waiting for commands...")

    buffer = ""

    while True:
        events = _poll.poll(50)
        if events:
            data = sys.stdin.read(1)
            if data:
                buffer += data

                commands, buffer = _drain_buffer(buffer)
                if not commands:
                    continue

                for cmd in commands:
                    process_command(cmd)


if __name__ == "__main__":
    if servo_driver is None:
        print("Fix the error above and reset the board.")
    else:
        main()
