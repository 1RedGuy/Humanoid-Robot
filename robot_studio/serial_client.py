import json
import time
import threading
import collections
from typing import Optional, List, Dict

import serial
import serial.tools.list_ports

_LOG_MAX_LINES = 200
_MAX_SERVOS_PER_SEND = 4  # 4 servos ~155B, under 256B buffer; both eyes blink together


class SerialClient:
    """Synchronous serial client that sends JSON commands to the ESP32."""

    def __init__(self, port: Optional[str] = None, baudrate: int = 115200):
        self.baudrate = baudrate
        self.port = port
        self.conn: Optional[serial.Serial] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._reader_stop = threading.Event()
        self._log: collections.deque = collections.deque(maxlen=_LOG_MAX_LINES)
        self._log_lock = threading.Lock()
        self._write_lock = threading.Lock()

    def _find_esp32_port(self) -> Optional[str]:
        ports = serial.tools.list_ports.comports()
        print(f"[serial] Available ports: {[(p.device, p.description) for p in ports]}")
        for p in ports:
            if any(tag in (p.description or "") for tag in ("ESP32", "CH340", "CP210")):
                return p.device
            if "usbserial" in p.device.lower() or "ttyUSB" in p.device:
                return p.device
        return None

    def connect(self) -> str:
        """Open the serial connection. Closes any existing connection first. Returns the port name used."""
        self.disconnect()
        resolved = self.port or self._find_esp32_port()
        if not resolved:
            raise ConnectionError("Could not find ESP32 serial port. Specify --port manually.")
        print(f"[serial] Opening {resolved} at {self.baudrate} baud...")
        self.conn = serial.Serial(resolved, self.baudrate, timeout=1)
        self.port = resolved
        self.clear_log()

        # Force ESP32 reset via DTR/RTS toggle (same sequence esptool uses)
        print("[serial] Resetting ESP32 via DTR/RTS...")
        self.conn.dtr = False
        self.conn.rts = True
        time.sleep(0.1)
        self.conn.rts = False
        time.sleep(0.05)
        self.conn.dtr = True

        self._start_reader()

        # Wait for ESP32 to boot and check for output
        time.sleep(2.0)
        waiting = self.conn.in_waiting
        log_lines = len(self._log)
        print(f"[serial] Port open. in_waiting={waiting} bytes, log_lines={log_lines}, reader running.")
        if log_lines == 0:
            print("[serial] WARNING: No ESP32 output detected after reset. "
                  "ESP32 may not be running, or main.py may have crashed on import.")
        return resolved

    def disconnect(self):
        """Close the serial connection and stop the reader thread."""
        self._stop_reader()
        if self.conn and self.conn.is_open:
            self.conn.close()
        self.conn = None

    def _start_reader(self):
        """Spawn a daemon thread that reads ESP32 serial output into the log."""
        self._stop_reader()
        self._reader_stop.clear()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def _stop_reader(self):
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_stop.set()
            self._reader_thread.join(timeout=2)
        self._reader_thread = None

    def _reader_loop(self):
        print("[serial-reader] Thread started")
        buf = ""
        self._esp32_error_count = 0
        while not self._reader_stop.is_set():
            try:
                if self.conn and self.conn.is_open and self.conn.in_waiting:
                    raw = self.conn.read(self.conn.in_waiting)
                    text = raw.decode("utf-8", errors="replace")
                    buf += text
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        line = line.rstrip("\r")
                        if line:
                            with self._log_lock:
                                self._log.append(line)
                            if "Invalid JSON" in line or "Error" in line:
                                self._esp32_error_count += 1
                                print(f"[ESP32 ERR #{self._esp32_error_count}] {line}")
                            else:
                                print(f"[ESP32] {line}")
                else:
                    self._reader_stop.wait(0.05)
            except Exception as e:
                print(f"[serial-reader] Error: {e}")
                self._reader_stop.wait(0.1)
        print("[serial-reader] Thread stopped")

    def get_log(self, last_n: int = 50) -> List[str]:
        """Return the most recent ESP32 output lines."""
        with self._log_lock:
            lines = list(self._log)
        return lines[-last_n:]

    def clear_log(self):
        with self._log_lock:
            self._log.clear()

    def _to_compact(self, data: dict) -> dict:
        """Shrink JSON keys to reduce payload size (fits 260B ESP32 buffer)."""
        cmd = data.get("command", "")
        if cmd == "move_servo":
            return {"c": cmd, "i": data["servo_id"], "a": round(data["angle"], 1), "d": round(data.get("duration", 0.5), 2)}
        if cmd == "move_multiple_servos":
            servos = [{"i": s["servo_id"], "a": round(s["angle"], 1), "d": round(s.get("duration", 0.5), 2)} for s in data["servos"]]
            return {"c": cmd, "s": servos}
        if cmd == "set_angles":
            servos = [{"i": s["servo_id"], "a": round(s["angle"], 1)} for s in data["servos"]]
            return {"c": cmd, "s": servos}
        return {"c": cmd}

    def _send(self, data: dict):
        with self._write_lock:
            if self.conn is None or not self.conn.is_open:
                raise ConnectionError("Serial connection not open")
            # Use compact keys to stay under ESP32 260B buffer
            compact = self._to_compact(data)
            line = json.dumps(compact, separators=(",", ":")) + "\n"
            payload = line.encode("utf-8")
            self.conn.write(payload)
            self.conn.flush()
            # Wire time + extra for ESP32 to process (char-by-char read + servo execution).
            wire_time = len(payload) * 10 / self.baudrate
            time.sleep(max(wire_time + 0.035, 0.050))
            cmd = data.get("command", "?")
            n_servos = len(data.get("servos", []))
            if n_servos:
                print(f"[serial-tx] {cmd} ({len(payload)}B, {n_servos} servos)")
            else:
                print(f"[serial-tx] {cmd} ({len(payload)}B)")

    def send_move_servo(self, pin: int, angle: float, duration: float = 0.5):
        self._send({
            "command": "move_servo",
            "servo_id": pin,
            "angle": round(angle, 1),
            "duration": round(duration, 2),
        })

    def send_move_multiple(self, servos: List[Dict]):
        """servos: list of {"servo_id": int, "angle": float, "duration": float}.
        Chunks to stay under ESP32 256B RX buffer."""
        for i in range(0, len(servos), _MAX_SERVOS_PER_SEND):
            chunk = servos[i : i + _MAX_SERVOS_PER_SEND]
            normalized = [
                {"servo_id": s["servo_id"], "angle": round(s["angle"], 1), "duration": round(s.get("duration", 0.5), 2)}
                for s in chunk
            ]
            self._send({"command": "move_multiple_servos", "servos": normalized})

    def send_set_angles(self, servos: List[Dict]):
        """Set servos to target angles immediately (no interpolation).
        Chunks to stay under ESP32 256B RX buffer."""
        for i in range(0, len(servos), _MAX_SERVOS_PER_SEND):
            chunk = servos[i : i + _MAX_SERVOS_PER_SEND]
            normalized = [{"servo_id": s["servo_id"], "angle": round(s["angle"], 1)} for s in chunk]
            self._send({"command": "set_angles", "servos": normalized})

    def send_calibrate(self):
        self._send({"command": "calibrate_servos"})

    def send_stop(self):
        self._send({"command": "stop"})

    @property
    def is_connected(self) -> bool:
        return self.conn is not None and self.conn.is_open
