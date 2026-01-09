"""
Servo Driver for PCA9685
Handles low-level I2C communication with PCA9685 and servo control
"""
import machine
import time
import json

# Try importing PCA9685Driver - adjust import path based on how you upload the library
try:
    from lib.pca9685.pca9685 import PCA9685Driver
except ImportError:
    try:
        from pca9685 import PCA9685Driver
    except ImportError:
        raise ImportError("Could not import PCA9685Driver. Make sure pca9685.py is uploaded to ESP32")

class ServoDriver:
    def __init__(self, config_file="servo_data.json"):
        """Initialize I2C and PCA9685, load servo configuration."""
        # ESP32 I2C pins (adjust if needed)
        # PCA9685Driver creates its own I2C bus, so we pass pin numbers
        self.pca = PCA9685Driver(i2c_channel=0, scl_pin=22, sda_pin=21, i2c_freq=400000)
        self.pca.set_pwm_frequency(50)  # 50Hz for servos
        
        # Load servo configuration
        self.servo_config = self._load_servo_config(config_file)
        
        # Store current positions
        self.current_positions = {}
        
    def _load_servo_config(self, filename):
        """
        Load servo configuration from JSON file.
        Only extracts the "servos" key from the JSON - ignores other data like "expression_data".
        """
        try:
            with open(filename, 'r') as f:
                config = json.load(f)
            # Extract only the "servos" section - other keys in JSON are ignored
            servo_config = config.get("servos", {})
            print(f"Loaded servo config from {filename} ({len(servo_config)} servos configured)")
            return servo_config
        except Exception as e:
            print(f"Warning: Could not load {filename}: {e}")
            print("Using default limits (0-180)")
            return {}
    
    def _get_servo_config(self, servo_id):
        """Get all configuration data for a servo. Returns dict with defaults."""
        servo_id_str = str(servo_id)
        if servo_id_str in self.servo_config:
            config = self.servo_config[servo_id_str]
            # If min/max are 0, use defaults (means not configured yet)
            min_angle = config.get("min_angle", 0)
            max_angle = config.get("max_angle", 0)
            if min_angle == 0 and max_angle == 0:
                min_angle = 0
                max_angle = 180
            
            return {
                "role": config.get("role"),
                "min_angle": min_angle,
                "max_angle": max_angle,
                "default_angle": config.get("default_angle", 90) if config.get("default_angle", 0) != 0 else 90,
                "inverted": config.get("inverted", False)
            }
        # Defaults if not configured
        return {
            "role": None,
            "min_angle": 0,
            "max_angle": 180,
            "default_angle": 90,
            "inverted": False
        }
    
    def _clamp_angle(self, servo_id, angle):
        """Clamp angle to servo-specific safe limits."""
        config = self._get_servo_config(servo_id)
        return max(config["min_angle"], min(config["max_angle"], angle))
    
    def _apply_inversion(self, servo_id, angle):
        """Apply inversion if servo is configured as inverted."""
        config = self._get_servo_config(servo_id)
        if config["inverted"]:
            # Invert: 0° becomes 180°, 180° becomes 0°
            return 180 - angle
        return angle
    
    def move_servo(self, servo_id, target_angle, duration=0.5):
        """
        Move servo smoothly from current position to target angle.
        
        Args:
            servo_id: PCA9685 channel (0-15)
            target_angle: Target angle in degrees
            duration: Movement duration in seconds
        """
        # Get servo config
        config = self._get_servo_config(servo_id)
        
        # Clamp to safe limits
        target_angle = self._clamp_angle(servo_id, target_angle)
        
        # Initialize position if needed
        if servo_id not in self.current_positions:
            self.current_positions[servo_id] = config["default_angle"]
        
        start_angle = self.current_positions[servo_id]
        steps = max(10, int(duration * 50))  # 50 steps per second
        
        for i in range(steps + 1):
            # Linear interpolation
            progress = i / steps
            current_angle = start_angle + (target_angle - start_angle) * progress
            
            # Apply inversion if needed
            actual_angle = self._apply_inversion(servo_id, current_angle)
            
            # Set servo angle via PCA9685
            self.pca.servo_set_angle(servo_id, actual_angle)
            time.sleep(duration / steps)
        
        self.current_positions[servo_id] = target_angle
    
    def calibrate_servos(self):
        """Calibration routine - move all servos to their default positions."""
        print("Calibrating servos...")
        for servo_id_str in self.servo_config.keys():
            servo_id = int(servo_id_str)
            config = self._get_servo_config(servo_id)
            default_angle = config["default_angle"]
            self.move_servo(servo_id, default_angle, duration=1.0)
        print("Calibration complete")
    
    def stop_all(self):
        """Emergency stop - stop all servo movements."""
        print("Emergency stop activated")
        # Set all servos to their current position (no movement)
        # Could also set to 0% duty cycle to completely stop
        for servo_id in self.current_positions.keys():
            current = self.current_positions[servo_id]
            actual_angle = self._apply_inversion(servo_id, current)
            self.pca.servo_set_angle(servo_id, actual_angle)
