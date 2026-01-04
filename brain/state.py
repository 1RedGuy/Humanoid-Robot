from datetime import datetime
import time

class RobotState: 
    def __init__(self):
        self.state = {
            "environment": {}
        }

    def _init_state(self):
        self.state = {
            "environment": {}
        }

    def get_environment(self):
        current_time = datetime.now()
        time_of_day = current_time.strftime("%H:%M:%S")
        state = self.state["environment"].copy()
        state["time_of_day"] = time_of_day
        return state

robot_state = RobotState()