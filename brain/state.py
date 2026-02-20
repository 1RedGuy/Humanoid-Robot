from datetime import datetime
import time


class RobotState:
    ACTIVITIES = ("idle", "listening", "thinking", "speaking")

    def __init__(self):
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

    def get_current_expression(self) -> str:
        return self.state["face"]["current_expression"]

    # ── activity ──

    def set_activity(self, activity: str):
        self.state["activity"] = activity

    def get_activity(self) -> str:
        return self.state["activity"]


robot_state = RobotState()