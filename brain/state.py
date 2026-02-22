import threading
from datetime import datetime
from typing import Callable, List


class RobotState:
    ACTIVITIES = ("idle", "listening", "thinking", "speaking")

    def __init__(self):
        self._observers: List[Callable] = []
        self._obs_lock = threading.Lock()
        self.state = {
            "environment": {},
            "face": {
                "current_expression": "neutral",
                "previous_expression": None,
            },
            "activity": "idle",
        }

    def _init_state(self):
        self.state = {
            "environment": {},
            "face": {
                "current_expression": "neutral",
                "previous_expression": None,
            },
            "activity": "idle",
        }

    # ── observers ──

    def add_observer(self, callback: Callable):
        with self._obs_lock:
            self._observers.append(callback)

    def remove_observer(self, callback: Callable):
        with self._obs_lock:
            try:
                self._observers.remove(callback)
            except ValueError:
                pass

    def _notify(self, event_type: str, data: dict):
        with self._obs_lock:
            observers = list(self._observers)
        for cb in observers:
            try:
                cb(event_type, data)
            except Exception:
                pass

    # ── environment ──

    def get_environment(self):
        current_time = datetime.now()
        time_of_day = current_time.strftime("%H:%M:%S")
        state = self.state["environment"].copy()
        state["time_of_day"] = time_of_day
        return state

    # ── face ──

    def set_expression(self, name: str):
        prev = self.state["face"]["current_expression"]
        self.state["face"]["previous_expression"] = prev
        self.state["face"]["current_expression"] = name
        if prev != name:
            self._notify("expression.changed", {"old": prev, "new": name})

    def get_current_expression(self) -> str:
        return self.state["face"]["current_expression"]

    # ── activity ──

    def set_activity(self, activity: str):
        old = self.state["activity"]
        self.state["activity"] = activity
        if old != activity:
            self._notify("activity.changed", {"old": old, "new": activity})

    def get_activity(self) -> str:
        return self.state["activity"]


robot_state = RobotState()
