import asyncio
import json
from dotenv import load_dotenv

from .audio.wake_word_detection.main import WakeWordDetection
from .conversation_manager.main import ConversationManager
from .initial_boot.initial_boot import InitialBoot
from .config import SERVO_DATA_PATH
from .state import robot_state
from .movement.servo_mixer import ServoMixer
from .movement.face_controller import FaceController
from .movement.behaviours.idle import IdleBehaviour

load_dotenv()


def _load_name_to_pin() -> dict[str, int]:
    try:
        with open(SERVO_DATA_PATH, "r") as f:
            data = json.load(f)
        return {
            name: int(cfg["pin"])
            for name, cfg in data.get("servos", {}).items()
            if cfg.get("pin") is not None
        }
    except Exception as e:
        print(f"[Brain] Could not load servo config: {e}")
        return {}


class Brain:
    def __init__(self):
        self.wake_word_detection = WakeWordDetection()
        self.initial_boot = InitialBoot()

        self.servo_controller = None
        self.mixer = None
        self.face_controller = None
        self.idle_behaviour = None
        self.conversation_manager = None

    async def run(self):
        """Main entry point for the Brain."""
        self.servo_controller = await self.initial_boot.run()

        if self.servo_controller:
            name_to_pin = _load_name_to_pin()
            self.mixer = ServoMixer(self.servo_controller, name_to_pin)
            self.face_controller = FaceController(self.mixer, SERVO_DATA_PATH)
            self.idle_behaviour = IdleBehaviour(self.mixer)
            self.face_controller.set_neutral(duration=1.0)
            robot_state.set_activity("idle")

        self.conversation_manager = ConversationManager(
            face_controller=self.face_controller,
        )

        async with asyncio.TaskGroup() as tg:
            if self.mixer:
                tg.create_task(self.mixer.run())
            if self.idle_behaviour:
                tg.create_task(self.idle_behaviour.run())
            tg.create_task(self._wake_word_loop())

    async def _wake_word_loop(self):
        """Continuously listen for wake word and start conversations."""
        while True:
            detected = await self.wake_word_detection.run()

            if detected:
                print("Wake word detected! Starting conversation...")
                await self.conversation_manager.run()
                print("Conversation ended. Listening for wake word again...")


if __name__ == "__main__":
    brain = Brain()
    asyncio.run(brain.run())
