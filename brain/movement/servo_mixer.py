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
import time
from typing import Callable, Dict, Optional

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
        on_event: Optional[Callable] = None,
    ):
        self._ctrl = servo_controller
        self._name_to_pin = name_to_pin
        self._pin_to_name = {v: k for k, v in name_to_pin.items()}
        self._lock = threading.Lock()
        self._layers: Dict[str, _Layer] = {}
        self._resolved: Dict[str, float] = {}
        self._queue: asyncio.Queue = asyncio.Queue()
        self._emit = on_event or (lambda t, d: None)
        self._send_count = 0
        self._error_count = 0

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

    def enqueue_instant_angles(self, targets: Dict[str, float]):
        """Enqueue an instant set_angles (no duration). Used for blink-open to match manual mode."""
        servos = []
        for name, angle in targets.items():
            pin = self._name_to_pin.get(name)
            if pin is not None:
                servos.append({"servo_id": pin, "angle": float(angle)})
        if servos:
            self._queue.put_nowait({"instant": True, "servos": servos})

    def get_resolved(self, servo_name: str) -> Optional[float]:
        """Return the currently resolved angle for a servo (highest-priority layer)."""
        with self._lock:
            return self._resolved.get(servo_name)

    _MIN_SEND_INTERVAL = 0.050  # 50 ms between serial writes — ESP32 needs time to drain its 256-byte RX buffer

    # ── async movement loop (run as a task) ─────────────────────────────

    async def run(self):
        """Drain the queue and send serial commands. Run as a long-lived task.

        Merges all queued items before each send so the ESP32 is never
        flooded with back-to-back writes.  A minimum interval between
        sends gives the microcontroller time to process each command.
        """
        loop = asyncio.get_event_loop()
        while True:
            item = await self._queue.get()
            if isinstance(item, dict) and item.get("instant"):
                servos = item.get("servos", [])
                if servos:
                    try:
                        await loop.run_in_executor(
                            None,
                            lambda s=servos: self._ctrl.set_angles(s),
                        )
                        self._send_count += 1
                    except Exception as e:
                        self._error_count += 1
                        names = [self._pin_to_name.get(m["servo_id"], f"pin{m['servo_id']}") for m in servos]
                        self._emit("servo.send_error", {"error": str(e), "servos": names})
                        print(f"[ServoMixer] set_angles error: {e}")
                await asyncio.sleep(self._MIN_SEND_INTERVAL)
                continue

            moves = item
            merged = {m["servo_id"]: m for m in moves}
            while not self._queue.empty():
                try:
                    extra = self._queue.get_nowait()
                    if isinstance(extra, dict) and extra.get("instant"):
                        self._queue.put_nowait(extra)
                        break
                    for m in extra:
                        merged[m["servo_id"]] = m
                except asyncio.QueueEmpty:
                    break
            batch = list(merged.values())
            try:
                await loop.run_in_executor(None, lambda b=batch: self._send_moves(b))
                self._send_count += 1
            except Exception as e:
                self._error_count += 1
                names = [self._pin_to_name.get(m["servo_id"], f"pin{m['servo_id']}") for m in batch]
                self._emit("servo.send_error", {
                    "error": str(e),
                    "servos": names,
                    "send_count": self._send_count,
                    "error_count": self._error_count,
                })
                print(f"[ServoMixer] send error #{self._error_count}: {e} (servos: {names})")
            await asyncio.sleep(self._MIN_SEND_INTERVAL)

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

    _MAX_SERVOS_PER_CMD = 4  # 4 servos ~155B, under 256B; both eyes blink together

    def _send_moves(self, moves: list[dict]):
        if not moves:
            return
        # Round duration to 2 decimals to shrink JSON payload
        for m in moves:
            m["duration"] = round(float(m["duration"]), 2)
        if len(moves) == 1:
            m = moves[0]
            self._ctrl.move_servo(m["servo_id"], m["angle"], m["duration"])
        else:
            # Chunk to avoid exceeding ESP32's 256-byte RX buffer
            for i in range(0, len(moves), self._MAX_SERVOS_PER_CMD):
                chunk = moves[i : i + self._MAX_SERVOS_PER_CMD]
                self._ctrl.move_multiple_servos(chunk)
