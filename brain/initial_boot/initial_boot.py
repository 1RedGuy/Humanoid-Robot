class InitialBoot:
    def __init__(self, surroundings_context):
        self.surroundings_context = surroundings_context

    async def run(self):
        await self.surroundings_context.run()
        # TODO: add any async startup logic needed for the robot here
        return



