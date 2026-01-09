import asyncio
from dotenv import load_dotenv
from .audio.wake_word_detection.main import WakeWordDetection
from .vision.surroundings_context_getter.main import SurroundingsContextGetter
from .conversation_manager.main import ConversationManager
from .initial_boot.initial_boot import InitialBoot

load_dotenv()


class Brain:
    def __init__(self):
        self.wake_word_detection = WakeWordDetection()
        self.conversation_manager = ConversationManager()
        self.initial_boot = InitialBoot()

    async def run(self):
        """Main entry point for the Brain."""
        await self.initial_boot.run()
        
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._wake_word_loop())
            tg.create_task(self.background_tasks())

    async def _wake_word_loop(self):
        """Continuously listen for wake word and start conversations."""
        while True:
            detected = await self.wake_word_detection.run()
            
            if detected:
                print("Wake word detected! Starting conversation...")
                await self.conversation_manager.run()
                print("Conversation ended. Listening for wake word again...")

    async def background_tasks(self):
        """Background tasks that run continuously."""
        while True:
            print("Background tasks running")
            await asyncio.sleep(4)


if __name__ == "__main__":
    brain = Brain()
    asyncio.run(brain.run())
