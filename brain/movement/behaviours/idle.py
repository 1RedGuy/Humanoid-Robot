"""
Idle behaviour — periodic blinking.

Uses the ServoMixer blink layer (priority 10) to temporarily override
eyelid servos.  When the layer is released after each blink, the eyelids
fall back to whatever the expression layer currently dictates.

Runs continuously regardless of activity state.
"""

import asyncio
import random
from typing import Dict

from brain.movement.servo_mixer import ServoMixer

BLINK_LAYER = "blink"
BLINK_PRIORITY = 10

EYELID_SERVOS = [
    "EyeLidLeftUp",
    "EyeLidLeftDown",
    "EyeLidRightUp",
    "EyeLidRightDown",
]

# Angles that represent "eyes closed" for each eyelid servo.
# These need to be tuned per-robot via manual_debug; these are starting
# defaults derived from min/max in servo_data.json.
EYELID_CLOSED: Dict[str, float] = {
    "EyeLidLeftUp": 90,
    "EyeLidLeftDown": 150,
    "EyeLidRightUp": 10,
    "EyeLidRightDown": 120,
}


class IdleBehaviour:
    def __init__(self, mixer: ServoMixer):
        self._mixer = mixer

    async def blink(self):
        close_duration = random.triangular(0.04, 0.08, 0.05)
        hold_duration = random.triangular(0.06, 0.15, 0.08)
        open_duration = random.triangular(0.04, 0.10, 0.06)

        self._mixer.set_layer(
            BLINK_LAYER,
            BLINK_PRIORITY,
            EYELID_CLOSED,
            duration=close_duration,
        )

        await asyncio.sleep(close_duration + hold_duration)

        self._mixer.release_layer(BLINK_LAYER, duration=open_duration)

        await asyncio.sleep(open_duration)

    async def run(self):
        """Blink loop — runs forever."""
        while True:
            interval = random.uniform(2.0, 6.0)
            await asyncio.sleep(interval)
            try:
                await self.blink()
            except Exception as e:
                print(f"[IdleBehaviour] blink error: {e}")
