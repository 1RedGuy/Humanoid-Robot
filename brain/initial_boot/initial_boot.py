from brain.vision.surroundings_context_getter.main import SurroundingsContextGetter
from brain.movement.servo_controller import ServoController
import asyncio

class InitialBoot:
    def __init__(self):
        self.surroundings_context = SurroundingsContextGetter()
        self.servo_controller = None

    async def _init_servo_controller(self):
        try:
            self.servo_controller = ServoController()
            print("Servo controller initialized successfully")
        except Exception as e:
            print(f"Warning: Could not initialize servo controller: {e}")
            print("Robot will continue without servo control")

    async def run(self):
        context_run = asyncio.create_task(self.surroundings_context.run())
        init_servo_run = asyncio.create_task(self._init_servo_controller())
        await asyncio.gather(context_run, init_servo_run)
        
        return self.servo_controller