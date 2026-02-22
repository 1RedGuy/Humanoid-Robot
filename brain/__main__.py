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
from .movement.lip_sync import LipSyncController

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


def _load_idle_config(config_path) -> tuple[dict, dict | None, dict, dict]:
    """Load idle section, gaze center, gaze limits, and eyes_closed from servo_data.json."""
    try:
        with open(config_path, "r") as f:
            data = json.load(f)
        raw = data.get("idle", {})
        idle_config = {
            "interval_min": float(raw.get("interval_min", 2.0)),
            "interval_max": float(raw.get("interval_max", 6.0)),
            "blink_chance": float(raw.get("blink_chance", 0.4)),
            "blink_close_min": float(raw.get("blink_close_min", 0.04)),
            "blink_close_max": float(raw.get("blink_close_max", 0.08)),
            "blink_hold_min": float(raw.get("blink_hold_min", 0.06)),
            "blink_hold_max": float(raw.get("blink_hold_max", 0.15)),
            "blink_open_min": float(raw.get("blink_open_min", 0.04)),
            "blink_open_max": float(raw.get("blink_open_max", 0.10)),
            "gaze_extent_fraction": float(raw.get("gaze_extent_fraction", 0.75)),
            "gaze_extent_x": float(raw.get("gaze_extent_x", 25)),
            "gaze_extent_y": float(raw.get("gaze_extent_y", 8)),
            "gaze_hold_min": float(raw.get("gaze_hold_min", 1.5)),
            "gaze_hold_max": float(raw.get("gaze_hold_max", 3.5)),
            "gaze_move_duration": float(raw.get("gaze_move_duration", 0.25)),
            "gaze_return_duration": float(raw.get("gaze_return_duration", 0.3)),
        }
        neutral = data.get("expressions", {}).get("neutral", {})
        gaze_center = None
        if "EyeXAxis" in neutral and "EyeYAxis" in neutral:
            gaze_center = {"EyeXAxis": float(neutral["EyeXAxis"]), "EyeYAxis": float(neutral["EyeYAxis"])}
        limits = {}
        for name in ("EyeXAxis", "EyeYAxis"):
            cfg = data.get("servos", {}).get(name, {})
            mn, mx = cfg.get("min_angle", 0), cfg.get("max_angle", 180)
            if mn is not None and mx is not None:
                limits[name] = (float(mn), float(mx))
        eyes_closed = data.get("expressions", {}).get("eyes_closed", {})
        eyelid_closed = {}
        for name in ("EyeLidLeftDown", "EyeLidLeftUp", "EyeLidRightDown", "EyeLidRightUp"):
            if name in eyes_closed and isinstance(eyes_closed[name], (int, float)):
                eyelid_closed[name] = float(eyes_closed[name])
        return idle_config, gaze_center, limits, eyelid_closed
    except Exception as e:
        print(f"[Brain] Could not load idle config: {e}")
        return {}, None, {}, {}


def _load_lip_sync_config(config_path) -> dict:
    """Load the lip_sync section from servo_data.json."""
    try:
        with open(config_path, "r") as f:
            data = json.load(f)
        return data.get("lip_sync", {})
    except Exception as e:
        print(f"[Brain] Could not load lip_sync config: {e}")
        return {}


class Brain:
    def __init__(self):
        self.wake_word_detection = WakeWordDetection()
        self.initial_boot = InitialBoot()

        self.servo_controller = None
        self.mixer = None
        self.face_controller = None
        self.lip_sync = None
        self.idle_behaviour = None
        self.conversation_manager = None

    async def run(self):
        """Main entry point for the Brain."""
        self.servo_controller = await self.initial_boot.run()

        if self.servo_controller:
            name_to_pin = _load_name_to_pin()
            self.mixer = ServoMixer(self.servo_controller, name_to_pin)
            self.face_controller = FaceController(self.mixer, SERVO_DATA_PATH)
            idle_config, gaze_center, gaze_limits, eyelid_closed = _load_idle_config(SERVO_DATA_PATH)
            idle_enabled_path = SERVO_DATA_PATH.parent.parent / "brain" / "data" / "idle_enabled.json"
            self.idle_behaviour = IdleBehaviour(
                self.mixer,
                idle_config=idle_config,
                gaze_center=gaze_center,
                gaze_limits=gaze_limits,
                eyelid_closed=eyelid_closed,
                idle_enabled_path=idle_enabled_path,
            )
            lip_sync_config = _load_lip_sync_config(SERVO_DATA_PATH)
            if lip_sync_config.get("enabled", True):
                self.lip_sync = LipSyncController(self.mixer, lip_sync_config)

            self.face_controller.set_neutral(duration=1.0)
            robot_state.set_activity("idle")

        self.conversation_manager = ConversationManager(
            face_controller=self.face_controller,
            lip_sync=self.lip_sync,
        )

        async with asyncio.TaskGroup() as tg:
            if self.mixer:
                tg.create_task(self.mixer.run())
            if self.idle_behaviour:
                tg.create_task(self.idle_behaviour.run())
            tg.create_task(self._wake_word_loop())

    async def _wink_right_eye(self):
        """Quick right-eye wink to acknowledge wake word detection."""
        if not self.mixer:
            return
        self.mixer.set_layer("wink", 10, {
            "EyeLidRightDown": 130,
            "EyeLidRightUp": 150,
        }, duration=0.08)
        await asyncio.sleep(0.2)
        self.mixer.release_layer("wink", duration=0.08)
        await asyncio.sleep(0.1)

    async def _wake_word_loop(self):
        """Continuously listen for wake word and start conversations."""
        while True:
            detected = await self.wake_word_detection.run()

            if detected:
                print("Wake word detected! Starting conversation...")
                await self._wink_right_eye()
                await self.conversation_manager.run()
                print("Conversation ended. Listening for wake word again...")


if __name__ == "__main__":
    brain = Brain()
    asyncio.run(brain.run())
