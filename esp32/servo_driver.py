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
        
        # Load full config (global + servos)
        self.servo_config, self.global_config = self._load_servo_config(config_file)
        
        # Store current positions
        self.current_positions = {}
        
        # Init: move all servos to global calibrate angle if enabled
        if self.global_config.get("calibrate_on_init", False):
            self._calibrate_on_init()
        
    def _load_servo_config(self, filename):
        """
        Load servo configuration from JSON file.
        Returns (servos dict, global dict). Init and default angle come from global, not per-servo default_angle.
        Also builds a pin -> config lookup so channel-based commands use correct limits.
        """
        try:
            with open(filename, 'r') as f:
                config = json.load(f)
            servo_config = config.get("servos", {})
            global_config = config.get("global", {})

            self.pin_to_config = {}
            for name, cfg in servo_config.items():
                pin = cfg.get("pin")
                if pin is not None:
                    self.pin_to_config[int(pin)] = cfg

            print(f"Loaded servo config from {filename} ({len(servo_config)} servos, global: {global_config})")
            return servo_config, global_config
        except Exception as e:
            print(f"Warning: Could not load {filename}: {e}")
            print("Using default limits (0-180)")
            self.pin_to_config = {}
            return {}, {}
    
    def _get_global_angle(self):
        """Angle used for init and when current position is unknown (from global, not per-servo default_angle)."""
        return self.global_config.get("calibrate_angle", 90)
    
    def _servo_id_from_key(self, key, config):
        """Resolve PCA channel from config key (name or number) and servo config (pin/role)."""
        chan = config.get("pin") or config.get("role")
        if chan is not None:
            return int(chan)
        try:
            return int(key)
        except (ValueError, TypeError):
            return None

    def _calibrate_on_init(self):
        """Move all configured servos to global calibrate_angle once at startup."""
        angle = self._get_global_angle()
        print(f"Calibrating all servos to global angle {angle}°...")
        for key, config in self.servo_config.items():
            servo_id = self._servo_id_from_key(key, config)
            if servo_id is None:
                continue
            self.current_positions[servo_id] = angle
            self.pca.servo_set_angle(servo_id, angle)
        print("Calibration complete")
    
    def _get_servo_config(self, servo_id):
        """Get all configuration data for a servo by PCA channel (pin number)."""
        default_angle = self._get_global_angle()
        if servo_id in self.pin_to_config:
            config = self.pin_to_config[servo_id]
            min_angle = config.get("min_angle", 0)
            max_angle = config.get("max_angle", 0)
            if min_angle == 0 and max_angle == 0:
                min_angle = 0
                max_angle = 180
            
            return {
                "role": config.get("role"),
                "min_angle": min_angle,
                "max_angle": max_angle,
                "default_angle": default_angle,
                "inverted": config.get("inverted", False)
            }
        return {
            "role": None,
            "min_angle": 0,
            "max_angle": 180,
            "default_angle": default_angle,
            "inverted": False
        }
    
    def _clamp_angle(self, servo_id, angle):
        """Clamp angle to servo-specific safe limits. Handles reversed min/max (e.g. 90->20)."""
        config = self._get_servo_config(servo_id)
        lo = min(config["min_angle"], config["max_angle"])
        hi = max(config["min_angle"], config["max_angle"])
        return max(lo, min(hi, angle))
    
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
        
        # Initialize position if needed (use global angle, not per-servo default_angle)
        if servo_id not in self.current_positions:
            self.current_positions[servo_id] = self._get_global_angle()
        
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
        """Calibration routine - move all servos to global calibrate_angle (not per-servo default_angle)."""
        angle = self._get_global_angle()
        print(f"Calibrating servos to global angle {angle}°...")
        for key, config in self.servo_config.items():
            servo_id = self._servo_id_from_key(key, config)
            if servo_id is None:
                continue
            self.move_servo(servo_id, angle, duration=1.0)
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
