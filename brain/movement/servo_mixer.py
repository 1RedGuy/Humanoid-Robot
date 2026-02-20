"""
Priority-based servo mixer.

Multiple controllers (expression, blink, gaze, ...) can claim servos at
different priority levels.  For each servo the highest-priority active layer
wins.  When a layer releases, the value falls back to the next layer's
*current* target — no old commands are replayed.

All outbound serial commands are funnelled through a single asyncio queue
so that the worker thread (conversation) and async tasks (blink) never race
on the serial port.
"""

import asyncio
import threading
from typing import Dict, Optional

from brain.movement.servo_controller import ServoController


class _Layer:
    __slots__ = ("name", "priority", "targets")

    def __init__(self, name: str, priority: int):
        self.name = name
        self.priority = priority
        self.targets: Dict[str, float] = {}


class ServoMixer:
    """
    Thread-safe layer stack with an async command queue.

    Parameters
    ----------
    servo_controller : ServoController
        The serial sender.
    name_to_pin : dict[str, int]
        Mapping from human servo name to PCA9685 channel.
    """

    def __init__(
        self,
        servo_controller: ServoController,
        name_to_pin: Dict[str, int],
    ):
        self._ctrl = servo_controller
        self._name_to_pin = name_to_pin
        self._lock = threading.Lock()
        self._layers: Dict[str, _Layer] = {}
        self._resolved: Dict[str, float] = {}
        self._queue: asyncio.Queue = asyncio.Queue()

    # ── public (thread-safe) ────────────────────────────────────────────

    def set_layer(
        self,
        layer_name: str,
        priority: int,
        targets: Dict[str, float],
        duration: float = 0.4,
    ):
        """Create or update a layer's targets and enqueue moves for affected servos."""
        with self._lock:
            layer = self._layers.get(layer_name)
            if layer is None:
                layer = _Layer(layer_name, priority)
                self._layers[layer_name] = layer
            layer.priority = priority
            layer.targets.update(targets)
            moves = self._resolve_and_diff(targets.keys(), duration)
        if moves:
            self._queue.put_nowait(moves)

    def release_layer(self, layer_name: str, duration: float = 0.3):
        """Remove a layer entirely; affected servos fall back to the next layer."""
        with self._lock:
            layer = self._layers.pop(layer_name, None)
            if layer is None:
                return
            affected = list(layer.targets.keys())
            moves = self._resolve_and_diff(affected, duration)
        if moves:
            self._queue.put_nowait(moves)

    def release_servos(
        self,
        layer_name: str,
        servo_names: list[str],
        duration: float = 0.3,
    ):
        """Release specific servos from a layer (layer stays for the rest)."""
        with self._lock:
            layer = self._layers.get(layer_name)
            if layer is None:
                return
            for n in servo_names:
                layer.targets.pop(n, None)
            if not layer.targets:
                del self._layers[layer_name]
            moves = self._resolve_and_diff(servo_names, duration)
        if moves:
            self._queue.put_nowait(moves)

    def get_resolved(self, servo_name: str) -> Optional[float]:
        """Return the currently resolved angle for a servo (highest-priority layer)."""
        with self._lock:
            return self._resolved.get(servo_name)

    # ── async movement loop (run as a task) ─────────────────────────────

    async def run(self):
        """Drain the queue and send serial commands. Run as a long-lived task."""
        while True:
            moves = await self._queue.get()
            try:
                self._send_moves(moves)
            except Exception as e:
                print(f"[ServoMixer] send error: {e}")

    # ── internals ───────────────────────────────────────────────────────

    def _resolve_servo(self, servo_name: str) -> Optional[float]:
        """Return angle from the highest-priority layer that has this servo."""
        best: Optional[_Layer] = None
        for layer in self._layers.values():
            if servo_name in layer.targets:
                if best is None or layer.priority > best.priority:
                    best = layer
        return best.targets[servo_name] if best else None

    def _resolve_and_diff(
        self,
        servo_names,
        duration: float,
    ) -> list[dict]:
        """Resolve affected servos; return move commands for those that changed."""
        moves = []
        for name in servo_names:
            new_val = self._resolve_servo(name)
            old_val = self._resolved.get(name)
            if new_val is None:
                self._resolved.pop(name, None)
                continue
            if old_val is None or abs(new_val - old_val) > 0.5:
                pin = self._name_to_pin.get(name)
                if pin is not None:
                    moves.append({
                        "servo_id": pin,
                        "angle": new_val,
                        "duration": duration,
                    })
                self._resolved[name] = new_val
        return moves

    def _send_moves(self, moves: list[dict]):
        if not moves:
            return
        if len(moves) == 1:
            m = moves[0]
            self._ctrl.move_servo(m["servo_id"], m["angle"], m["duration"])
        else:
            self._ctrl.move_multiple_servos(moves)
