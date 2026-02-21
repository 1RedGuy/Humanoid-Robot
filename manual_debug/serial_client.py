import json
import time
import threading
import collections
from typing import Optional, List, Dict

import serial
import serial.tools.list_ports

_LOG_MAX_LINES = 200


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

    def _send(self, data: dict):
        if self.conn is None or not self.conn.is_open:
            raise ConnectionError("Serial connection not open")
        line = json.dumps(data) + "\n"
        self.conn.write(line.encode("utf-8"))
        self.conn.flush()
        cmd = data.get("command", "?")
        print(f"[serial-tx] {cmd} ({len(line)} bytes)")

    def send_move_servo(self, pin: int, angle: float, duration: float = 0.5):
        self._send({
            "command": "move_servo",
            "servo_id": pin,
            "angle": angle,
            "duration": duration,
        })

    def send_move_multiple(self, servos: List[Dict]):
        """servos: list of {"servo_id": int, "angle": float, "duration": float}"""
        self._send({
            "command": "move_multiple_servos",
            "servos": servos,
        })

    def send_set_angles(self, servos: List[Dict]):
        """Set servos to target angles immediately (no interpolation).

        servos: list of {"servo_id": int, "angle": float}
        """
        self._send({
            "command": "set_angles",
            "servos": servos,
        })

    def send_calibrate(self):
        self._send({"command": "calibrate_servos"})

    def send_stop(self):
        self._send({"command": "stop"})

    @property
    def is_connected(self) -> bool:
        return self.conn is not None and self.conn.is_open
