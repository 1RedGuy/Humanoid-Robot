import json
import serial
import serial.tools.list_ports
from typing import List, Dict, Optional


class ServoController:
    def __init__(self, port: Optional[str] = None, baudrate: int = 115200):
        """
        Initialize ServoController with serial connection to ESP32.
        
        Args:
            port: Serial port path (e.g., '/dev/ttyUSB0' or '/dev/cu.usbserial-*')
                 If None, will attempt to auto-detect ESP32
            baudrate: Serial baud rate (default 115200)
        """
        self.baudrate = baudrate
        self.port = port or self._find_esp32_port()
        
        if not self.port:
            raise ValueError("Could not find ESP32 serial port. Please specify port manually.")
        
        self.serial_conn = serial.Serial(self.port, self.baudrate, timeout=1)
        print(f"Connected to ESP32 on {self.port}")

    def _find_esp32_port(self) -> Optional[str]:
        """Attempt to auto-detect ESP32 serial port."""
        ports = serial.tools.list_ports.comports()
        for port in ports:
            # Common ESP32 identifiers
            if 'ESP32' in port.description or 'CH340' in port.description or 'CP210' in port.description:
                return port.device
            # Also check common USB serial patterns
            if 'usbserial' in port.device.lower() or 'ttyUSB' in port.device:
                return port.device
        return None

    def _send_command(self, command_data: Dict):
        """Send JSON command to ESP32 via serial."""
        command_json = json.dumps(command_data) + '\n'
        self.serial_conn.write(command_json.encode('utf-8'))

    def calibrate_servos(self):
        """Send calibration command to ESP32."""
        self._send_command({"command": "calibrate_servos"})

    def move_servo(self, servo_id: int, angle: float, duration: float = 0.5):
        self._send_command({
            "command": "move_servo",
            "servo_id": servo_id,
            "angle": angle,
            "duration": duration,
        })

    def move_multiple_servos(self, servo_commands: List[Dict]):
        servos = []
        for cmd in servo_commands:
            servos.append({
                "servo_id": cmd["servo_id"],
                "angle": cmd["angle"],
                "duration": cmd.get("duration", 0.5),
            })
        self._send_command({
            "command": "move_multiple_servos",
            "servos": servos,
        })

    def set_angles(self, servo_commands: List[Dict]):
        """Set servos to target angles immediately (no interpolation)."""
        servos = [{"servo_id": c["servo_id"], "angle": c["angle"]} for c in servo_commands]
        self._send_command({"command": "set_angles", "servos": servos})

    def stop_all(self):
        """Send emergency stop command."""
        self._send_command({"command": "stop"})

    def close(self):
        """Close serial connection."""
        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()