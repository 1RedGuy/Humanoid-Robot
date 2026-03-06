"""
Entry point when running ``python -m brain``.

Delegates entirely to brain.main.Brain so there is a single source of truth.
"""
import asyncio

from dotenv import load_dotenv

load_dotenv()

from .main import Brain  # noqa: E402 — must be after load_dotenv

if __name__ == "__main__":
    brain = Brain()
    asyncio.run(brain.run())
