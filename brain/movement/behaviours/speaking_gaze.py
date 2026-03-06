"""
Speaking gaze behaviour — subtle eye saccades while the robot is talking.

Runs as an async task; activates only when ``robot_state.activity == "speaking"``.
Uses the ``speaking_gaze`` mixer layer at priority 6, which sits above the
idle_gaze layer (priority 5) but below blink/wink (priority 10), so it
provides gentle saccade activity without interfering with blinking.

Configuration is read from servo_data.json under the ``speaking_gaze`` key:
    enabled         : bool   (default true)
    interval_min    : float  seconds between saccades (default 0.8)
    interval_max    : float  (default 3.0)
    extent_fraction : float  fraction of idle gaze range to use (default 0.20)
    move_duration   : float  saccade snap duration in seconds (default 0.08)
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import Dict

from brain.movement.servo_mixer import ServoMixer
from brain.state import robot_state

SPEAKING_GAZE_LAYER = "speaking_gaze"
SPEAKING_GAZE_PRIORITY = 6


class SpeakingGaze:
    """Subtle saccade-style eye movements during speech.

    Parameters
    ----------
    mixer :
        Shared priority servo mixer.
    config_path :
        Path to ``servo_data.json``.
    """

    def __init__(self, mixer: ServoMixer, config_path: Path):
        self._mixer = mixer
        self._config_path = config_path
        self._center: Dict[str, float] = {}
        self._limits: Dict[str, tuple] = {}
        self._cfg: Dict = {}
        self._load_config()

    def _load_config(self):
        try:
            with open(self._config_path, "r") as f:
                data = json.load(f)

            neutral = data.get("expressions", {}).get("neutral", {})
            if "EyeXAxis" in neutral:
                self._center["EyeXAxis"] = float(neutral["EyeXAxis"])
            if "EyeYAxis" in neutral:
                self._center["EyeYAxis"] = float(neutral["EyeYAxis"])

            for name in ("EyeXAxis", "EyeYAxis"):
                cfg = data.get("servos", {}).get(name, {})
                mn = cfg.get("min_angle")
                mx = cfg.get("max_angle")
                if mn is not None and mx is not None:
                    lo, hi = min(float(mn), float(mx)), max(float(mn), float(mx))
                    self._limits[name] = (lo, hi)

            self._cfg = data.get("speaking_gaze", {})
        except Exception as e:
            print(f"[SpeakingGaze] Could not load config: {e}")

    def _clamp(self, name: str, value: float) -> float:
        lim = self._limits.get(name)
        if lim is None:
            return value
        return max(lim[0], min(lim[1], value))

    async def run(self):
        """Async loop — drives subtle eye saccades only while speaking."""
        while True:
            if not self._cfg.get("enabled", True):
                await asyncio.sleep(0.5)
                continue

            activity = robot_state.get_activity()

            if activity != "speaking":
                # Release the layer so idle_gaze can take over again
                self._mixer.release_layer(SPEAKING_GAZE_LAYER, duration=0.10)
                await asyncio.sleep(0.10)
                continue

            cx = self._center.get("EyeXAxis")
            cy = self._center.get("EyeYAxis")
            if cx is None or cy is None:
                await asyncio.sleep(0.2)
                continue

            extent_fraction = float(self._cfg.get("extent_fraction", 0.20))
            move_duration = float(self._cfg.get("move_duration", 0.08))
            interval_min = float(self._cfg.get("interval_min", 0.8))
            interval_max = float(self._cfg.get("interval_max", 3.0))

            lim_x = self._limits.get("EyeXAxis")
            lim_y = self._limits.get("EyeYAxis")
            ext_x = extent_fraction * (lim_x[1] - lim_x[0]) / 2.0 if lim_x else extent_fraction * 25.0
            ext_y = extent_fraction * (lim_y[1] - lim_y[0]) / 2.0 if lim_y else extent_fraction * 8.0

            x = self._clamp("EyeXAxis", cx + random.uniform(-ext_x, ext_x))
            y = self._clamp("EyeYAxis", cy + random.uniform(-ext_y, ext_y))

            self._mixer.set_layer(
                SPEAKING_GAZE_LAYER,
                SPEAKING_GAZE_PRIORITY,
                {"EyeXAxis": x, "EyeYAxis": y},
                duration=move_duration,
            )

            await asyncio.sleep(random.uniform(interval_min, interval_max))
