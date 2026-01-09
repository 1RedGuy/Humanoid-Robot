import asyncio, random

class IdleBehaviour: 
    def __init__(self, servo_controller):
        self.servo_controller = servo_controller
        self.eye_servos = [1, 2] # CHANGE TO THE ACTUAL SERVO IDS


        async def blink(self):

            blink_duration = random.triangular(0.1, 0.4, 0.18)

            for servo_id in self.eye_servos:
                open_duration = random.triangular(0.15, 0.30, 0.20)
                await self.servo_controller.move_servo(servo_id, 0, open_duration) # Move the servo to 0 degrees for 0.1 seconds

            await asyncio.sleep(blink_duration)

            for servo_id in self.eye_servos:
                close_duration = random.triangular(0.07, 0.10, 0.08)
                await self.servo_controller.move_servo(servo_id, 0, close_duration) # Move the servo to 0 degrees for 0.1 seconds

            await asyncio.sleep(random.uniform(1, 3))

    async def run(self):
        pass