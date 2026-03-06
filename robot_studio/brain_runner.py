"""
Brain lifecycle manager for Robot Studio.

Starts / stops the Brain as asyncio tasks within the FastAPI event loop,
using the shared SerialClient (wrapped in an adapter) instead of creating
its own serial connection.
"""

import asyncio
import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from .event_bus import EventBus
from .serial_client import SerialClient

_LOGS_DIR = Path(__file__).resolve().parent.parent / "brain" / "data" / "logs"


class ServoControllerAdapter:
    """Wraps SerialClient so it satisfies the interface that Brain's
    ServoMixer expects from ``brain.movement.servo_controller.ServoController``."""

    def __init__(self, serial_client: SerialClient):
        self._s = serial_client

    def move_servo(self, servo_id: int, angle: float, duration: float = 0.5):
        self._s.send_move_servo(servo_id, angle, duration)

    def move_multiple_servos(self, servo_commands):
        self._s.send_move_multiple(servo_commands)

    def set_angles(self, servo_commands):
        self._s.send_set_angles(servo_commands)

    def stop_all(self):
        self._s.send_stop()

    def close(self):
        pass


class BrainRunner:
    """Manages starting and stopping the Brain from within Robot Studio."""

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._event_bus: Optional[EventBus] = None
        self._state_observer = None
        self._log_subscriber = None
        self._log_file = None
        self.running = False
        self.brain = None

    def _open_session_log(self):
        _LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = _LOGS_DIR / f"{ts}_session.jsonl"
        self._log_file = open(path, "a", encoding="utf-8")

    def _close_session_log(self):
        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

    def _write_log_event(self, event):
        if self._log_file:
            try:
                self._log_file.write(json.dumps(event.to_dict(), default=str) + "\n")
                self._log_file.flush()
            except Exception:
                pass

    async def start(self, serial_client: SerialClient, event_bus: EventBus):
        if self.running:
            return
        self._event_bus = event_bus
        adapter = ServoControllerAdapter(serial_client)

        self._open_session_log()

        self._log_subscriber = lambda evt: self._write_log_event(evt)
        event_bus.subscribe(self._log_subscriber)

        from brain.state import robot_state
        from brain.main import Brain

        self._state_observer = lambda t, d: event_bus.publish(t, d)
        robot_state.add_observer(self._state_observer)

        def on_event(event_type: str, data: dict):
            event_bus.publish(event_type, data)

        brain = Brain(servo_controller=adapter, on_event=on_event)

        self._task = asyncio.create_task(self._run_brain(brain, event_bus))
        self.running = True
        event_bus.publish("brain.started", {})

    async def _run_brain(self, brain, event_bus: EventBus):
        self.brain = brain
        try:
            await brain.run()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            event_bus.publish("brain.error", {"error": str(e), "traceback": traceback.format_exc()})
            print(f"[BrainRunner] Brain crashed: {e}")
        finally:
            self.brain = None
            self.running = False

    async def stop(self):
        if not self.running and self._task is None:
            return

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        self._task = None
        self.running = False

        from brain.state import robot_state
        if self._state_observer:
            robot_state.remove_observer(self._state_observer)
            self._state_observer = None

        robot_state._init_state()

        if self._event_bus:
            self._event_bus.publish("brain.stopped", {})
            if self._log_subscriber:
                self._event_bus.unsubscribe(self._log_subscriber)
                self._log_subscriber = None

        self._close_session_log()
