import json
from typing import Optional, List, Dict

import serial
import serial.tools.list_ports


class SerialClient:
    """Synchronous serial client that sends JSON commands to the ESP32."""

    def __init__(self, port: Optional[str] = None, baudrate: int = 115200):
        self.baudrate = baudrate
        self.port = port
        self.conn: Optional[serial.Serial] = None

    def _find_esp32_port(self) -> Optional[str]:
        ports = serial.tools.list_ports.comports()
        for p in ports:
            if any(tag in (p.description or "") for tag in ("ESP32", "CH340", "CP210")):
                return p.device
            if "usbserial" in p.device.lower() or "ttyUSB" in p.device:
                return p.device
        return None

    def connect(self) -> str:
        """Open the serial connection. Returns the port name used."""
        resolved = self.port or self._find_esp32_port()
        if not resolved:
            raise ConnectionError("Could not find ESP32 serial port. Specify --port manually.")
        self.conn = serial.Serial(resolved, self.baudrate, timeout=1)
        self.port = resolved
        return resolved

    def _send(self, data: dict):
        if self.conn is None or not self.conn.is_open:
            raise ConnectionError("Serial connection not open")
        line = json.dumps(data) + "\n"
        self.conn.write(line.encode("utf-8"))

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

    def send_calibrate(self):
        self._send({"command": "calibrate_servos"})

    def send_stop(self):
        self._send({"command": "stop"})

    def close(self):
        if self.conn and self.conn.is_open:
            self.conn.close()

    @property
    def is_connected(self) -> bool:
        return self.conn is not None and self.conn.is_open
