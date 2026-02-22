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

# Note: REPL UART buffer size is fixed at 260 bytes in MicroPython firmware.
# To use 1500 bytes: build custom firmware with modified mphalport.c
# (change stdin_ringbuf_array[260] to stdin_ringbuf_array[1500]).
# See esp32/BUILD_CUSTOM_FIRMWARE.md for instructions.

_poll = select.poll()
_poll.register(sys.stdin, select.POLLIN)

servo_driver = None
try:
    from servo_driver import ServoDriver
    servo_driver = ServoDriver()
except Exception as e:
    print("ServoDriver init failed:", e)

_MOVE_COMMANDS = ("move_servo", "move_multiple_servos", "set_angles")


def _normalize_cmd(raw):
    """Expand compact keys (c,s,i,a,d) to full format for servo_driver."""
    cmd = raw.get("c") or raw.get("command")
    if cmd == "move_servo":
        return {"command": cmd, "servo_id": raw.get("i", raw.get("servo_id")),
                "angle": raw.get("a", raw.get("angle")),
                "duration": raw.get("d", raw.get("duration", 0.5))}
    if cmd in ("move_multiple_servos", "set_angles"):
        servos = raw.get("s") or raw.get("servos", [])
        out = []
        for s in servos:
            o = {"servo_id": s.get("i", s.get("servo_id")), "angle": s.get("a", s.get("angle"))}
            if "d" in s or "duration" in s:
                o["duration"] = s.get("d", s.get("duration", 0.5))
            out.append(o)
        return {"command": cmd, "servos": out}
    return {"command": cmd}


def process_command(command_data):
    """Process incoming JSON command and execute servo movement."""
    if servo_driver is None:
        print("No driver (init failed), ignoring command")
        return
    try:
        cmd = _normalize_cmd(command_data)
        command = cmd.get("command")

        if command == "move_servo":
            servo_id = cmd["servo_id"]
            angle = cmd["angle"]
            duration = cmd.get("duration", 0.5)
            servo_driver.move_servo(servo_id, angle, duration)

        elif command == "set_angles":
            n = len(cmd.get("servos", []))
            print("set_angles", n, "servos")
            servo_driver.set_angles(cmd["servos"])

        elif command == "move_multiple_servos":
            servos = cmd["servos"]
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


_invalid_json_count = 0


def _parse_json_line(line):
    """Parse one or more JSON objects from a line (handles concatenated objects with no newline)."""
    global _invalid_json_count
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
                _invalid_json_count += 1
                n = len(rest)
                head = rest[:60] if n > 60 else rest
                tail = rest[-40:] if n > 100 else ""
                n_braces = rest.count("{") - rest.count("}")
                print("Invalid JSON #%d (%dB, brace_diff=%d): %s ... %s" % (
                    _invalid_json_count, n, n_braces, head, tail))
                break
            try:
                out.append(json.loads(rest[: idx + 1]))
            except Exception:
                _invalid_json_count += 1
                part = rest[: idx + 1]
                n_braces = part.count("{") - part.count("}")
                print("Invalid JSON #%d (split fail, %dB, brace_diff=%d): %s..." % (
                    _invalid_json_count, len(part), n_braces, part[:70]))
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

    buf_parts = []
    buffer = ""

    while True:
        events = _poll.poll(50)
        if events:
            # Collect all available bytes into a list (avoids quadratic
            # string concatenation in MicroPython) then join once.
            buf_parts.clear()
            while True:
                ch = sys.stdin.read(1)
                if ch:
                    buf_parts.append(ch)
                else:
                    break
                if not _poll.poll(0):
                    break

            if buf_parts:
                buffer += "".join(buf_parts)

            commands, buffer = _drain_buffer(buffer)
            for cmd in commands:
                process_command(cmd)


if __name__ == "__main__":
    if servo_driver is None:
        print("Fix the error above and reset the board.")
    else:
        main()
