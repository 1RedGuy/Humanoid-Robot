import asyncio
from dotenv import load_dotenv

from .audio.wake_word_detection.main import WakeWordDetection
from .vision.surroundings_context_getter.main import SurroundingsContextGetter
from .initial_boot.initial_boot import InitialBoot

load_dotenv()

class Brain:
    def __init__(self):
        self.wake_word_detection = WakeWordDetection()
        self.surroundings_context = SurroundingsContextGetter()
        self.surroundings_context_file = None
        self.initial_boot = InitialBoot()
        self.running = True

    async def run(self):
        await self.initial_boot.run()
        
        wake_word_task = asyncio.create_task(self._wake_word_loop())
        context_task = asyncio.create_task(self._context_loop())
        
        await asyncio.gather(wake_word_task, context_task)

    async def _wake_word_loop(self):
        while self.running:
            if await self.wake_word_detection.run():
                print("Wake word detected!")
                print("Executing action...")
                self.running = False
                break

    async def _context_loop(self):
        print("Starting context loop...")
        while self.running:
            try:
                self.surroundings_context_file = await self.surroundings_context.run()
                print(f"Context file: {self.surroundings_context_file}")
            except Exception as e:
                print(f"Error getting context: {e}")
            
            await asyncio.sleep(60)

if __name__ == "__main__":
    brain = Brain()
    asyncio.run(brain.run())