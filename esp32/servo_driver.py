"""
Servo Driver for PCA9685
Handles low-level I2C communication with PCA9685 and servo control
"""
import machine
import time
import json
from machine import PWM, Pin

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
        self.pca = PCA9685Driver(i2c_channel=0, scl_pin=22, sda_pin=21, i2c_freq=400000)
        self.pca.set_pwm_frequency(50)  # 50Hz for servos

        # I2C diagnostic: scan bus and verify PCA writes face channels correctly
        try:
            devs = self.pca.i2c.scan()
            print("I2C scan:", [hex(d) for d in devs])
        except Exception as e:
            print("I2C scan error:", e)
        self._pca_readback_test()

        # GPIO PWM instances for direct-wired servos {gpio_pin: PWM}
        self.gpio_pwm = {}
        self.gpio_pins = set()

        # Load full config (global + servos + expressions)
        self.servo_config, self.global_config, self.neutral_expression = self._load_servo_config(config_file)

        # Store current positions
        self.current_positions = {}

        # Apply neutral to PCA servos BEFORE initialising GPIO/LEDC PWM.
        if self.global_config.get("calibrate_on_init", False) and self.neutral_expression:
            self._apply_neutral()

        # Init GPIO AFTER PCA neutral is applied so I2C is undisturbed.
        self._init_gpio_servos()
        
    def _pca_readback_test(self):
        """Write 90° to ch0 (jaw) and ch1 (face), read back registers.
        If both match → PCA writes are fine, issue is hardware downstream.
        If face channel mismatches → I2C/PCA fault on that channel.
        """
        period_ms = 20.0
        for ch in (0, 1):
            angle = 90.0
            on_time_ms = 1.0 + (angle / 360.0) * 2.0
            expected = round(on_time_ms * 4095 / period_ms)
            try:
                self.pca.servo_set_angle(ch, angle)
                off_l = self.pca._read_reg(ch * 4 + 8)
                off_h = self.pca._read_reg(ch * 4 + 9)
                actual = off_l | ((off_h & 0x0F) << 8)
                ok = abs(actual - expected) <= 1
                print("PCA ch%d: wrote 90deg => reg=%d (expect %d) %s" % (
                    ch, actual, expected, "OK" if ok else "MISMATCH"))
            except Exception as e:
                print("PCA ch%d readback error: %s" % (ch, e))

    def _init_gpio_servos(self):
        """Create machine.PWM objects for servos wired directly to GPIO pins."""
        for name, cfg in self.servo_config.items():
            if cfg.get("driver") == "gpio":
                pin = cfg.get("pin")
                if pin is None:
                    continue
                gpio_pin = int(pin)
                freq = int(cfg.get("freq", 50))
                try:
                    self.gpio_pwm[gpio_pin] = PWM(Pin(gpio_pin), freq=freq)
                    self.gpio_pins.add(gpio_pin)
                    print(f"GPIO servo '{name}' on pin {gpio_pin} @ {freq}Hz")
                except Exception as e:
                    print(f"GPIO servo '{name}' pin {gpio_pin} init failed: {e}")

    def _angle_to_duty_u16(self, angle, min_pulse_us, max_pulse_us, period_us, min_angle, max_angle):
        """Convert servo angle to 16-bit duty cycle.

        Maps angle linearly from [min_angle, max_angle] to [min_pulse_us, max_pulse_us],
        then converts to a duty_u16 value using the actual PWM period.
        """
        angle_range = max_angle - min_angle if max_angle != min_angle else 180.0
        t = (angle - min_angle) / angle_range
        t = max(0.0, min(1.0, t))
        pulse_us = min_pulse_us + t * (max_pulse_us - min_pulse_us)
        return int(pulse_us / period_us * 65535)

    def _set_servo_angle_hw(self, servo_id, physical_angle):
        """Route a servo angle command to PCA9685 or GPIO PWM."""
        if servo_id in self.gpio_pins:
            pwm = self.gpio_pwm.get(servo_id)
            if pwm is not None:
                cfg = self.pin_to_config.get(servo_id, {})
                min_pulse = cfg.get("min_pulse_us", 1000.0)
                max_pulse = cfg.get("max_pulse_us", 2000.0)
                freq = int(cfg.get("freq", 50))
                period_us = 1_000_000.0 / freq
                min_angle = cfg.get("min_angle", 0)
                max_angle = cfg.get("max_angle", 180)
                pwm.duty_u16(self._angle_to_duty_u16(
                    physical_angle, min_pulse, max_pulse, period_us, min_angle, max_angle))
        else:
            self.pca.servo_set_angle(servo_id, physical_angle)

    def _load_servo_config(self, filename):
        """
        Load servo configuration from JSON file.
        Returns (servos dict, global dict, neutral dict). Init: apply neutral when calibrate_on_init.
        """
        try:
            with open(filename, 'r') as f:
                config = json.load(f)
            servo_config = config.get("servos", {})
            global_config = config.get("global", {})
            neutral = config.get("expressions", {}).get("neutral", {})

            self.pin_to_config = {}
            for name, cfg in servo_config.items():
                pin = cfg.get("pin")
                if pin is not None:
                    self.pin_to_config[int(pin)] = cfg

            print(f"Loaded servo config from {filename} ({len(servo_config)} servos, global: {global_config})")
            return servo_config, global_config, neutral
        except Exception as e:
            print(f"Warning: Could not load {filename}: {e}")
            print("Using default limits (0-180)")
            self.pin_to_config = {}
            return {}, {}, {}
    
    def _get_global_angle(self):
        """Angle used for init and when current position is unknown (from global, not per-servo default_angle)."""
        return self.global_config.get("calibrate_angle", 90)
    
    def _servo_id_from_key(self, key, config):
        """Resolve PCA channel from config key (name or number) and servo config."""
        pin = config.get("pin")
        if pin is not None:
            return int(pin)
        try:
            return int(key)
        except (ValueError, TypeError):
            return None

    def _apply_neutral(self):
        """Apply expressions.neutral so servos that have a neutral pose (e.g. eyes center + open) go there."""
        if not self.neutral_expression:
            return
        print("Applying neutral pose...")
        for name, angle in self.neutral_expression.items():
            cfg = self.servo_config.get(name)
            if cfg is None:
                continue
            pin = cfg.get("pin")
            if pin is None:
                continue
            try:
                servo_id = int(pin)
                angle = float(angle)
                clamped = self._clamp_angle(servo_id, angle)
                self.current_positions[servo_id] = clamped
                physical = self._apply_inversion(servo_id, clamped)
                self._set_servo_angle_hw(servo_id, physical)
            except Exception as e:
                print(f"neutral {name}: {e}")
        print("Neutral pose applied")

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
                "min_angle": min_angle,
                "max_angle": max_angle,
                "default_angle": default_angle,
                "inverted": config.get("inverted", False)
            }
        return {
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
        """Invert within the servo's own [min, max] range so physical angles stay in bounds."""
        config = self._get_servo_config(servo_id)
        if config["inverted"]:
            return config["min_angle"] + config["max_angle"] - angle
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
            
            self._set_servo_angle_hw(servo_id, actual_angle)
            time.sleep(duration / steps)
        
        self.current_positions[servo_id] = target_angle

    def move_multiple_servos(self, servo_commands, duration=0.5):
        """
        Move multiple servos at once so they move in sync (same timing).
        servo_commands: list of {"servo_id": int, "angle": float, "duration": float (optional)}
        Uses the longest duration if different durations are given.
        """
        if not servo_commands:
            return

        max_dur = duration
        targets = []
        for cmd in servo_commands:
            sid = cmd.get("servo_id")
            if sid is None:
                continue
            sid = int(sid)
            angle = self._clamp_angle(sid, float(cmd.get("angle", self._get_global_angle())))
            targets.append((sid, angle))
            max_dur = max(max_dur, float(cmd.get("duration", duration)))

        if not targets:
            return

        for sid, _a in targets:
            if sid not in self.current_positions:
                self.current_positions[sid] = self._get_global_angle()

        # Snapshot start angles once so the interpolation base never shifts.
        start_angles = {sid: self.current_positions[sid] for sid, _a in targets}

        steps = max(10, int(max_dur * 50))
        step_duration = max_dur / steps

        for i in range(steps + 1):
            progress = i / steps
            for sid, target_angle in targets:
                current_angle = start_angles[sid] + (target_angle - start_angles[sid]) * progress
                self._set_servo_angle_hw(sid, self._apply_inversion(sid, current_angle))
            time.sleep(step_duration)

        for sid, target_angle in targets:
            self.current_positions[sid] = target_angle

    def set_angles(self, servo_commands):
        """Set multiple servos to target angles immediately (no interpolation).

        Ideal for real-time slider control where the stream of events
        already provides smooth motion and blocking would cause lag.
        """
        for cmd in servo_commands:
            sid = cmd.get("servo_id")
            if sid is None:
                continue
            try:
                sid = int(sid)
                angle = self._clamp_angle(sid, float(cmd.get("angle", self._get_global_angle())))
                self._set_servo_angle_hw(sid, self._apply_inversion(sid, angle))
                self.current_positions[sid] = angle
            except Exception as e:
                print("set_angles servo", sid, "err:", e)

    def calibrate_servos(self):
        """Snap all servos to their calibrate angle: per-servo calibrate_angle if set, else global."""
        default_angle = self._get_global_angle()
        print("Calibrating servos...")
        for key, config in self.servo_config.items():
            servo_id = self._servo_id_from_key(key, config)
            if servo_id is None:
                continue
            angle = config.get("calibrate_angle")
            if angle is None:
                angle = default_angle
            else:
                angle = float(angle)
            clamped = self._clamp_angle(servo_id, angle)
            self.current_positions[servo_id] = clamped
            self._set_servo_angle_hw(servo_id, self._apply_inversion(servo_id, clamped))
        print("Calibration complete")
    
    def stop_all(self):
        """Emergency stop - stop all servo movements."""
        print("Emergency stop activated")
        # Set all servos to their current position (no movement)
        # Could also set to 0% duty cycle to completely stop
        for servo_id in self.current_positions.keys():
            current = self.current_positions[servo_id]
            actual_angle = self._apply_inversion(servo_id, current)
            self._set_servo_angle_hw(servo_id, actual_angle)
