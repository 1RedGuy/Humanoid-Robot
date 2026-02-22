"""
Idle behaviour — periodic blinking and random gaze (look left/right).

All timing and angles come from servo_data.json (idle section and expressions.eyes_closed).
Uses the ServoMixer blink layer (priority 10) and idle_gaze layer (priority 5).
When idle_enabled_path is set, a JSON file is read each loop to allow toggling idle on/off from Robot Studio.
"""

import asyncio
import json
import random
from pathlib import Path
from typing import Callable, Dict, Optional

from brain.movement.servo_mixer import ServoMixer
from brain.state import robot_state

BLINK_LAYER = "blink"
BLINK_PRIORITY = 10
GAZE_LAYER = "idle_gaze"
GAZE_PRIORITY = 5

EYELID_SERVO_NAMES = ("EyeLidLeftDown", "EyeLidLeftUp", "EyeLidRightDown", "EyeLidRightUp")


class IdleBehaviour:
    def __init__(
        self,
        mixer: ServoMixer,
        idle_config: Optional[Dict] = None,
        gaze_center: Optional[Dict[str, float]] = None,
        gaze_limits: Optional[Dict[str, tuple]] = None,
        eyelid_closed: Optional[Dict[str, float]] = None,
        eyelid_open: Optional[Dict[str, float]] = None,
        idle_enabled_path: Optional[Path] = None,
        on_event: Optional[Callable] = None,
    ):
        self._mixer = mixer
        self._idle = idle_config or {}
        self._gaze_center = gaze_center or {}
        self._gaze_limits = gaze_limits or {}
        self._eyelid_closed = eyelid_closed or {}
        self._eyelid_open = eyelid_open or {}
        self._idle_enabled_path = Path(idle_enabled_path) if idle_enabled_path else None
        self._emit = on_event or (lambda t, d: None)

    def _is_idle_enabled(self) -> bool:
        if self._idle_enabled_path is None or not self._idle_enabled_path.exists():
            return True
        try:
            with open(self._idle_enabled_path, "r") as f:
                data = json.load(f)
            return bool(data.get("idle_enabled", True))
        except Exception:
            return True

    def _clamp(self, name: str, value: float) -> float:
        lim = self._gaze_limits.get(name)
        if lim is None:
            return value
        return max(lim[0], min(lim[1], value))

    async def random_look(self):
        """Look to a random direction within extent (by default ~75% of axis range), hold, then return to center."""
        cx = self._gaze_center.get("EyeXAxis")
        cy = self._gaze_center.get("EyeYAxis")
        if cx is None or cy is None:
            return
        fraction = self._idle.get("gaze_extent_fraction", 0.75)
        ext_x = self._idle.get("gaze_extent_x", 25)
        ext_y = self._idle.get("gaze_extent_y", 8)
        lim_x = self._gaze_limits.get("EyeXAxis")
        lim_y = self._gaze_limits.get("EyeYAxis")
        if lim_x is not None:
            ext_x = fraction * (lim_x[1] - lim_x[0]) / 2
        if lim_y is not None:
            ext_y = fraction * (lim_y[1] - lim_y[0]) / 2
        x = self._clamp("EyeXAxis", cx + random.uniform(-ext_x, ext_x))
        y = self._clamp("EyeYAxis", cy + random.uniform(-ext_y, ext_y))
        move_dur = self._idle.get("gaze_move_duration", 0.25)
        return_dur = self._idle.get("gaze_return_duration", 0.3)
        hold_min = self._idle.get("gaze_hold_min", 1.5)
        hold_max = self._idle.get("gaze_hold_max", 3.5)
        self._mixer.set_layer(GAZE_LAYER, GAZE_PRIORITY, {"EyeXAxis": x, "EyeYAxis": y}, duration=move_dur)
        await asyncio.sleep(random.uniform(hold_min, hold_max))
        self._mixer.set_layer(
            GAZE_LAYER, GAZE_PRIORITY, {"EyeXAxis": cx, "EyeYAxis": cy}, duration=return_dur
        )

    async def blink(self):
        if not self._eyelid_closed:
            return
        close_min = self._idle.get("blink_close_min", 0.04)
        close_max = self._idle.get("blink_close_max", 0.08)
        hold_min = self._idle.get("blink_hold_min", 0.06)
        hold_max = self._idle.get("blink_hold_max", 0.15)
        open_min = self._idle.get("blink_open_min", 0.04)
        open_max = self._idle.get("blink_open_max", 0.10)
        close_duration = random.triangular(close_min, close_max, (close_min + close_max) / 2)
        hold_duration = random.triangular(hold_min, hold_max, (hold_min + hold_max) / 2)
        open_duration = random.triangular(open_min, open_max, (open_min + open_max) / 2)
        self._mixer.set_layer(
            BLINK_LAYER,
            BLINK_PRIORITY,
            self._eyelid_closed,
            duration=close_duration,
        )
        await asyncio.sleep(close_duration + hold_duration)
        # Use instant set_angles for open (same as manual mode) — smaller payload,
        # no duration, more reliable than release_layer fallback.
        if self._eyelid_open:
            self._mixer.enqueue_instant_angles(self._eyelid_open)
            await asyncio.sleep(open_duration + 0.05)
        self._mixer.release_layer(BLINK_LAYER, duration=0.0)

    async def run(self):
        """Idle loop — blink and random gaze.

        Respects both the idle_enabled file (manual toggle) and the current
        robot activity so that servos stay silent during listening.
        """
        interval_min = self._idle.get("interval_min", 2.0)
        interval_max = self._idle.get("interval_max", 6.0)
        blink_chance = self._idle.get("blink_chance", 0.4)
        while True:
            if not self._is_idle_enabled():
                await asyncio.sleep(0.5)
                continue

            activity = robot_state.get_activity()

            if activity in ("listening", "speaking"):
                await asyncio.sleep(0.1)
                continue

            interval = random.uniform(interval_min, interval_max)
            await asyncio.sleep(interval)

            # Re-check after sleeping — state may have changed.
            activity = robot_state.get_activity()
            if activity in ("listening", "speaking"):
                continue

            # During thinking only blink (no gaze) to stay alive
            # without shifting eye focus away from the user.
            blink_only = activity == "thinking"

            do_gaze = (
                not blink_only
                and self._gaze_center
                and "EyeXAxis" in self._gaze_center
                and "EyeYAxis" in self._gaze_center
                and random.random() > blink_chance
            )
            try:
                if do_gaze:
                    await self.random_look()
                    self._emit("idle.gaze", {})
                else:
                    await self.blink()
                    self._emit("idle.blink", {})
            except Exception as e:
                print(f"[IdleBehaviour] idle error: {e}")
