"""
Neck controller — head yaw (left/right) and pitch (up/down).

Controls NeckYaw and NeckPitch servos wired directly to ESP32 GPIO pins.
Provides imperative movement methods and an async idle drift behaviour that
produces subtle, organic head movement.

Layer priorities (for reference):
  expression  : 0
  neck_idle   : 3   ← this module
  idle_gaze   : 5
  blink       : 10
  lip_sync    : 7
"""

import asyncio
import json
import random
import time
from pathlib import Path
from typing import Dict, Optional

from brain.movement.servo_mixer import ServoMixer
from brain.state import robot_state

NECK_LAYER = "neck"
NECK_IDLE_LAYER = "neck_idle"
NECK_PRIORITY = 3
NECK_IDLE_PRIORITY = 3

NECK_SERVO_NAMES = ("NeckYaw", "NeckPitch")


class NeckController:
    """High-level neck movement controller.

    Parameters
    ----------
    mixer : ServoMixer
        Shared priority mixer.
    config_path : Path
        Path to servo_data.json — used to load neutral angles and servo limits.
    """

    def __init__(self, mixer: ServoMixer, config_path: Path):
        self._mixer = mixer
        self._config_path = config_path
        self._neutral: Dict[str, float] = {"NeckYaw": 180.0, "NeckPitch": 200.0}
        self._limits: Dict[str, tuple] = {}
        self._load_config()

    def _load_config(self):
        try:
            with open(self._config_path, "r") as f:
                data = json.load(f)
            servos = data.get("servos", {})
            for name in NECK_SERVO_NAMES:
                cfg = servos.get(name, {})
                # neutral_angle on the servo entry takes priority over expressions.neutral
                neutral_angle = cfg.get("neutral_angle")
                if neutral_angle is not None:
                    self._neutral[name] = float(neutral_angle)
                else:
                    expr_neutral = data.get("expressions", {}).get("neutral", {})
                    if name in expr_neutral:
                        self._neutral[name] = float(expr_neutral[name])
                mn = cfg.get("min_angle")
                mx = cfg.get("max_angle")
                if mn is not None and mx is not None:
                    lo, hi = min(float(mn), float(mx)), max(float(mn), float(mx))
                    self._limits[name] = (lo, hi)
        except Exception as e:
            print(f"[NeckController] Could not load config: {e}")

    def _clamp(self, name: str, value: float) -> float:
        lim = self._limits.get(name)
        if lim is None:
            return value
        return max(lim[0], min(lim[1], value))

    def _neutral_yaw(self) -> float:
        return self._neutral.get("NeckYaw", 90.0)

    def _neutral_pitch(self) -> float:
        return self._neutral.get("NeckPitch", 90.0)

    # ── imperative movement ──────────────────────────────────────────────

    def look_at(self, yaw: float, pitch: float, duration: float = 0.5):
        """Move head to specific yaw and pitch angles."""
        targets = {
            "NeckYaw": self._clamp("NeckYaw", yaw),
            "NeckPitch": self._clamp("NeckPitch", pitch),
        }
        self._mixer.set_layer(NECK_LAYER, NECK_PRIORITY, targets, duration=duration)

    def look_left(self, amount: float = 1.0, duration: float = 0.5):
        """Turn head left. amount in [0, 1] scales within the yaw range."""
        cy = self._neutral_yaw()
        lim = self._limits.get("NeckYaw", (0, 180))
        offset = amount * (cy - lim[0])
        angle = self._clamp("NeckYaw", cy - offset)
        self._mixer.set_layer(NECK_LAYER, NECK_PRIORITY, {"NeckYaw": angle}, duration=duration)

    def look_right(self, amount: float = 1.0, duration: float = 0.5):
        """Turn head right. amount in [0, 1] scales within the yaw range."""
        cy = self._neutral_yaw()
        lim = self._limits.get("NeckYaw", (0, 180))
        offset = amount * (lim[1] - cy)
        angle = self._clamp("NeckYaw", cy + offset)
        self._mixer.set_layer(NECK_LAYER, NECK_PRIORITY, {"NeckYaw": angle}, duration=duration)

    def look_up(self, amount: float = 1.0, duration: float = 0.5):
        """Tilt head up. amount in [0, 1] scales within the pitch range."""
        cp = self._neutral_pitch()
        lim = self._limits.get("NeckPitch", (0, 180))
        offset = amount * (lim[1] - cp)
        angle = self._clamp("NeckPitch", cp + offset)
        self._mixer.set_layer(NECK_LAYER, NECK_PRIORITY, {"NeckPitch": angle}, duration=duration)

    def look_down(self, amount: float = 1.0, duration: float = 0.5):
        """Tilt head down. amount in [0, 1] scales within the pitch range."""
        cp = self._neutral_pitch()
        lim = self._limits.get("NeckPitch", (0, 180))
        offset = amount * (cp - lim[0])
        angle = self._clamp("NeckPitch", cp - offset)
        self._mixer.set_layer(NECK_LAYER, NECK_PRIORITY, {"NeckPitch": angle}, duration=duration)

    def center(self, duration: float = 0.5):
        """Return head to neutral position."""
        self._mixer.set_layer(
            NECK_LAYER,
            NECK_PRIORITY,
            {"NeckYaw": self._neutral_yaw(), "NeckPitch": self._neutral_pitch()},
            duration=duration,
        )

    # ── expressive actions (blocking — call from a thread) ────────────────

    def nod(self, offset: float = 12.0):
        """Brief downward nod then return to neutral.

        Blocking — intended to be called from a daemon thread so it does not
        block the asyncio event loop.  Uses the ``neck_action`` layer at
        priority 8 so it overrides idle drift but not blink/wink (priority 10).

        Parameters
        ----------
        offset:
            Degrees of pitch movement from neutral (default 12°).
        """
        _ACTION_LAYER = "neck_action"
        _ACTION_PRIORITY = 8

        cp = self._neutral_pitch()
        nod_angle = self._clamp("NeckPitch", cp - offset)

        self._mixer.set_layer(_ACTION_LAYER, _ACTION_PRIORITY, {"NeckPitch": nod_angle}, duration=0.20)
        time.sleep(0.30)
        self._mixer.set_layer(_ACTION_LAYER, _ACTION_PRIORITY, {"NeckPitch": cp}, duration=0.25)
        time.sleep(0.35)
        self._mixer.release_layer(_ACTION_LAYER, duration=0.10)

    def shake(self, offset: float = 15.0):
        """Left-right-centre head shake then return to neutral.

        Blocking — intended to be called from a daemon thread.

        Parameters
        ----------
        offset:
            Degrees of yaw movement to each side from neutral (default 15°).
        """
        _ACTION_LAYER = "neck_action"
        _ACTION_PRIORITY = 8

        cy = self._neutral_yaw()
        left_angle = self._clamp("NeckYaw", cy - offset)
        right_angle = self._clamp("NeckYaw", cy + offset)

        self._mixer.set_layer(_ACTION_LAYER, _ACTION_PRIORITY, {"NeckYaw": left_angle}, duration=0.20)
        time.sleep(0.30)
        self._mixer.set_layer(_ACTION_LAYER, _ACTION_PRIORITY, {"NeckYaw": right_angle}, duration=0.25)
        time.sleep(0.40)
        self._mixer.set_layer(_ACTION_LAYER, _ACTION_PRIORITY, {"NeckYaw": cy}, duration=0.20)
        time.sleep(0.30)
        self._mixer.release_layer(_ACTION_LAYER, duration=0.10)

    # ── idle drift behaviour ─────────────────────────────────────────────

    async def run(self):
        """Async idle loop — subtle random head drift.

        Picks small random offsets from neutral every few seconds, moves there
        slowly, holds, then returns. Pauses while the robot is actively
        listening or speaking.
        """
        # Drift limits: small fraction of the full range
        YAW_DRIFT = 12.0    # ± degrees from neutral
        PITCH_DRIFT = 6.0   # ± degrees from neutral

        while True:
            activity = robot_state.get_activity()

            if activity in ("listening", "speaking"):
                # Hold center while actively talking or listening
                await asyncio.sleep(0.1)
                continue

            # Wait a random interval before the next drift
            interval = random.uniform(3.0, 8.0)
            await asyncio.sleep(interval)

            activity = robot_state.get_activity()
            if activity in ("listening", "speaking"):
                continue

            # Pick a random target near neutral
            yaw_target = self._clamp(
                "NeckYaw",
                self._neutral_yaw() + random.uniform(-YAW_DRIFT, YAW_DRIFT),
            )
            pitch_target = self._clamp(
                "NeckPitch",
                self._neutral_pitch() + random.uniform(-PITCH_DRIFT, PITCH_DRIFT),
            )

            move_dur = random.uniform(0.5, 1.0)
            hold_dur = random.uniform(1.5, 4.0)
            return_dur = random.uniform(0.4, 0.8)

            self._mixer.set_layer(
                NECK_IDLE_LAYER,
                NECK_IDLE_PRIORITY,
                {"NeckYaw": yaw_target, "NeckPitch": pitch_target},
                duration=move_dur,
            )
            await asyncio.sleep(move_dur + hold_dur)

            # Check again before returning — state may have changed
            if robot_state.get_activity() not in ("listening", "speaking"):
                self._mixer.set_layer(
                    NECK_IDLE_LAYER,
                    NECK_IDLE_PRIORITY,
                    {"NeckYaw": self._neutral_yaw(), "NeckPitch": self._neutral_pitch()},
                    duration=return_dur,
                )
