"""
Idle behaviour — periodic blinking and random gaze (look left/right).

Improvements over original:
  • Saccade-style gaze: fast eye-snap (``gaze_saccade_duration``) instead of
    slow drift, more natural and human-like.
  • Horizontal bias: 70 % chance of horizontal-only saccade (``gaze_x_bias``).
  • Speaking blinks: blink at random intervals while speaking so eyes don't
    look frozen during speech.
  • Thinking gaze: look up-and-to-the-side while the robot is "thinking"
    (``thinking_gaze_chance``).
  • Double blink: 15 % chance of a second blink after a short pause
    (``double_blink_chance``).
  • Neck coordination: when a saccade deviates far from centre (>
    ``neck_coord_threshold`` fraction), nudge the neck slightly in the same
    direction for more natural head-eye coupling.

All timing and angles come from servo_data.json (idle section and
expressions.eyes_closed).  Uses the ServoMixer blink layer (priority 10)
and idle_gaze layer (priority 5).

When ``idle_enabled_path`` is set, a JSON file is read each loop to allow
toggling idle on/off from Robot Studio.
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
        neck_controller=None,  # optional NeckController for eye-neck coordination
    ):
        self._mixer = mixer
        self._idle = idle_config or {}
        self._gaze_center = gaze_center or {}
        self._gaze_limits = gaze_limits or {}
        self._eyelid_closed = eyelid_closed or {}
        self._eyelid_open = eyelid_open or {}
        self._idle_enabled_path = Path(idle_enabled_path) if idle_enabled_path else None
        self._emit = on_event or (lambda t, d: None)
        self._neck = neck_controller

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

    # ── gaze ──────────────────────────────────────────────────────────────

    async def random_look(self):
        """Saccade-style random gaze — fast snap to target, hold, return.

        Uses ``gaze_saccade_duration`` (fast) for the snap instead of the
        slower ``gaze_move_duration``.  Applies a horizontal bias so the
        robot looks left/right more than up/down.  When the deviation is
        large enough, nudges the neck slightly in the same direction.
        """
        cx = self._gaze_center.get("EyeXAxis")
        cy = self._gaze_center.get("EyeYAxis")
        if cx is None or cy is None:
            return

        fraction = self._idle.get("gaze_extent_fraction", 0.75)
        ext_x = self._idle.get("gaze_extent_x", 25.0)
        ext_y = self._idle.get("gaze_extent_y", 8.0)
        lim_x = self._gaze_limits.get("EyeXAxis")
        lim_y = self._gaze_limits.get("EyeYAxis")

        if lim_x is not None:
            ext_x = fraction * (lim_x[1] - lim_x[0]) / 2.0
        if lim_y is not None:
            ext_y = fraction * (lim_y[1] - lim_y[0]) / 2.0

        # 70% chance: horizontal-only saccade (more natural)
        gaze_x_bias = self._idle.get("gaze_x_bias", 0.70)
        horizontal_only = random.random() < gaze_x_bias

        x = self._clamp("EyeXAxis", cx + random.uniform(-ext_x, ext_x))
        y = cy if horizontal_only else self._clamp("EyeYAxis", cy + random.uniform(-ext_y, ext_y))

        saccade_dur = self._idle.get("gaze_saccade_duration", 0.10)
        return_dur = self._idle.get("gaze_return_duration", 0.3)
        hold_min = self._idle.get("gaze_hold_min", 1.5)
        hold_max = self._idle.get("gaze_hold_max", 3.5)

        angles: Dict[str, float] = {"EyeXAxis": x}
        if not horizontal_only:
            angles["EyeYAxis"] = y

        self._mixer.set_layer(GAZE_LAYER, GAZE_PRIORITY, angles, duration=saccade_dur)

        # Neck coordination: if deviation is large, nudge neck slightly same direction
        if self._neck is not None and ext_x > 0:
            neck_threshold = self._idle.get("neck_coord_threshold", 0.60)
            deviation = abs(x - cx) / ext_x
            if deviation > neck_threshold:
                nudge = (deviation - neck_threshold) / (1.0 - neck_threshold + 1e-9) * 0.25
                if x > cx:
                    self._neck.look_right(nudge, duration=saccade_dur)
                else:
                    self._neck.look_left(nudge, duration=saccade_dur)

        await asyncio.sleep(random.uniform(hold_min, hold_max))
        self._mixer.set_layer(
            GAZE_LAYER, GAZE_PRIORITY, {"EyeXAxis": cx, "EyeYAxis": cy}, duration=return_dur
        )

    async def _thinking_look(self):
        """Upward-and-to-the-side gaze that mimics the look of someone thinking."""
        cx = self._gaze_center.get("EyeXAxis")
        cy = self._gaze_center.get("EyeYAxis")
        if cx is None or cy is None:
            return

        lim_x = self._gaze_limits.get("EyeXAxis")
        lim_y = self._gaze_limits.get("EyeYAxis")

        # Horizontal: 25 % of full range, random side
        ext_x = (lim_x[1] - lim_x[0]) * 0.125 if lim_x else 15.0
        # Vertical: 35 % of range toward minimum == "looking up"
        ext_y_up = (cy - lim_y[0]) * 0.35 if lim_y else 10.0

        x = self._clamp("EyeXAxis", cx + random.choice([-1, 1]) * random.uniform(ext_x * 0.5, ext_x))
        y = self._clamp("EyeYAxis", cy - random.uniform(ext_y_up * 0.5, ext_y_up))

        saccade_dur = self._idle.get("gaze_saccade_duration", 0.10)
        return_dur = self._idle.get("gaze_return_duration", 0.3)
        hold_min = self._idle.get("gaze_hold_min", 1.5)
        hold_max = self._idle.get("gaze_hold_max", 3.5)

        self._mixer.set_layer(GAZE_LAYER, GAZE_PRIORITY, {"EyeXAxis": x, "EyeYAxis": y}, duration=saccade_dur)
        await asyncio.sleep(random.uniform(hold_min, hold_max))
        self._mixer.set_layer(
            GAZE_LAYER, GAZE_PRIORITY, {"EyeXAxis": cx, "EyeYAxis": cy}, duration=return_dur
        )

    # ── blink ─────────────────────────────────────────────────────────────

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
        # Use instant set_angles for open — reliable, no duration, no dropped frame
        if self._eyelid_open:
            self._mixer.enqueue_instant_angles(self._eyelid_open)
            await asyncio.sleep(open_duration + 0.05)
        self._mixer.release_layer(BLINK_LAYER, duration=0.0)

    # ── main loop ─────────────────────────────────────────────────────────

    async def run(self):
        """Idle loop — blink and random gaze.

        Behaviour per activity state:
        • ``listening``  — completely still (no blink, no gaze)
        • ``speaking``   — blink only at longer intervals (3-7 s), no gaze shift
        • ``thinking``   — blink + occasional upward thinking gaze, no random gaze
        • ``idle``       — saccade gaze + blink + double blink, neck coordination
        """
        interval_min = self._idle.get("interval_min", 2.0)
        interval_max = self._idle.get("interval_max", 6.0)
        blink_chance = self._idle.get("blink_chance", 0.4)
        double_blink_chance = self._idle.get("double_blink_chance", 0.15)
        thinking_gaze_chance = self._idle.get("thinking_gaze_chance", 0.45)
        speaking_blink_min = self._idle.get("speaking_blink_interval_min", 3.0)
        speaking_blink_max = self._idle.get("speaking_blink_interval_max", 7.0)

        while True:
            if not self._is_idle_enabled():
                await asyncio.sleep(0.5)
                continue

            activity = robot_state.get_activity()

            # ── speaking: allow natural blinks so the eyes don't look frozen ──
            if activity == "speaking":
                await asyncio.sleep(random.uniform(speaking_blink_min, speaking_blink_max))
                if robot_state.get_activity() == "speaking":
                    try:
                        await self.blink()
                        self._emit("idle.blink", {"context": "speaking"})
                    except Exception as e:
                        print(f"[IdleBehaviour] speaking blink error: {e}")
                continue

            # ── listening: stay completely still ──
            if activity == "listening":
                await asyncio.sleep(0.1)
                continue

            # ── idle / thinking: normal cadence ──
            interval = random.uniform(interval_min, interval_max)
            await asyncio.sleep(interval)

            # Re-check after sleeping
            activity = robot_state.get_activity()
            if activity in ("listening", "speaking"):
                continue

            blink_only = activity == "thinking"

            # Thinking gaze: upward look during "thinking" state
            do_thinking_gaze = (
                blink_only
                and self._gaze_center
                and "EyeXAxis" in self._gaze_center
                and "EyeYAxis" in self._gaze_center
                and random.random() < thinking_gaze_chance
            )

            # Regular saccade gaze (idle only)
            do_gaze = (
                not blink_only
                and self._gaze_center
                and "EyeXAxis" in self._gaze_center
                and "EyeYAxis" in self._gaze_center
                and random.random() > blink_chance
            )

            try:
                if do_thinking_gaze:
                    await self._thinking_look()
                    self._emit("idle.gaze", {"type": "thinking"})
                elif do_gaze:
                    await self.random_look()
                    self._emit("idle.gaze", {})
                else:
                    await self.blink()
                    self._emit("idle.blink", {})
                    # Optional double blink (more lifelike)
                    if random.random() < double_blink_chance:
                        await asyncio.sleep(random.uniform(0.10, 0.25))
                        if robot_state.get_activity() not in ("listening",):
                            await self.blink()
                            self._emit("idle.blink", {"double": True})
            except Exception as e:
                print(f"[IdleBehaviour] idle error: {e}")
