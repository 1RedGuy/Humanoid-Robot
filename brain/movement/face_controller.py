"""
High-level face / expression controller.

Loads expression definitions from servo_data.json and applies them via
the ServoMixer's priority layer system.  Thread-safe — can be called from
the conversation worker thread or from async tasks.
"""

import json
import time
from pathlib import Path
from typing import Dict, Optional

from brain.state import robot_state
from brain.movement.servo_mixer import ServoMixer

EXPRESSION_LAYER = "expression"
EXPRESSION_PRIORITY = 0


class FaceController:
    def __init__(self, mixer: ServoMixer, config_path: Path):
        self._mixer = mixer
        self._config_path = config_path
        self._expressions: Dict[str, Dict[str, float]] = {}
        self._name_to_pin: Dict[str, int] = {}
        self._load_config()

    def _load_config(self):
        with open(self._config_path, "r") as f:
            data = json.load(f)
        self._expressions = data.get("expressions", {})
        servos = data.get("servos", {})
        self._name_to_pin = {}
        for name, cfg in servos.items():
            pin = cfg.get("pin")
            if pin is not None:
                self._name_to_pin[name] = int(pin)

    def reload_config(self):
        self._load_config()

    @property
    def available_expressions(self) -> list[str]:
        return list(self._expressions.keys())

    def get_expression_angles(self, name: str) -> Optional[Dict[str, float]]:
        return self._expressions.get(name)

    def set_expression(self, name: str, duration: float = 0.4):
        """
        Apply a named expression.  Reads angles from servo_data.json and
        pushes them into the mixer's expression layer.

        If the expression has no angles (e.g. ``"neutral": {}``), the
        mixer's expression layer is cleared so lower layers (if any)
        can take over — in practice this means servos hold their last
        position until something else moves them.
        """
        angles = self._expressions.get(name)
        if angles is None:
            print(f"[FaceController] unknown expression: {name}")
            return

        robot_state.set_expression(name)

        if not angles:
            self._mixer.release_layer(EXPRESSION_LAYER, duration=duration)
            return

        self._mixer.set_layer(
            EXPRESSION_LAYER,
            EXPRESSION_PRIORITY,
            angles,
            duration=duration,
        )

    def set_neutral(self, duration: float = 0.4):
        self.set_expression("neutral", duration=duration)

    # ── wink animations ───────────────────────────────────────────────────

    def wink_right(self):
        """Close and reopen the right eye (wink).

        Blocking — call from a thread or via ``asyncio.to_thread``.
        Uses the ``wink_right`` expression for the closed position,
        falling back to right-eye entries from ``eyes_closed``.
        """
        # Prefer dedicated wink_right expression; fall back to eyes_closed right eye
        closed = {
            k: v
            for k, v in (
                self._expressions.get("wink_right")
                or {
                    k: v
                    for k, v in self._expressions.get("eyes_closed", {}).items()
                    if "Right" in k
                }
            ).items()
            if isinstance(v, (int, float))
        }
        if not closed:
            return

        open_angles = {
            k: v
            for k, v in self._expressions.get("eyes_open", {}).items()
            if "Right" in k and isinstance(v, (int, float))
        }

        self._mixer.set_layer("wink", 10, closed, duration=0.06)
        time.sleep(0.06 + 0.14)  # close duration + hold

        if open_angles:
            self._mixer.enqueue_instant_angles(open_angles)
            time.sleep(0.08 + 0.05)  # open duration + settle

        self._mixer.release_layer("wink", duration=0.0)

    def wink_left(self):
        """Close and reopen the left eye (wink).

        Blocking — call from a thread or via ``asyncio.to_thread``.
        Uses the ``wink_left`` expression for the closed position,
        falling back to left-eye entries from ``eyes_closed``.
        """
        closed = {
            k: v
            for k, v in (
                self._expressions.get("wink_left")
                or {
                    k: v
                    for k, v in self._expressions.get("eyes_closed", {}).items()
                    if "Left" in k
                }
            ).items()
            if isinstance(v, (int, float))
        }
        if not closed:
            return

        open_angles = {
            k: v
            for k, v in self._expressions.get("eyes_open", {}).items()
            if "Left" in k and isinstance(v, (int, float))
        }

        self._mixer.set_layer("wink", 10, closed, duration=0.06)
        time.sleep(0.06 + 0.14)  # close duration + hold

        if open_angles:
            self._mixer.enqueue_instant_angles(open_angles)
            time.sleep(0.08 + 0.05)  # open duration + settle

        self._mixer.release_layer("wink", duration=0.0)
