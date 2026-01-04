import asyncio
import base64
import json
import os
import time
from pathlib import Path

import cv2
from openai import OpenAI

from brain.config import SurroundingsContextGetterPrompt
from brain.state import robot_state

class SurroundingsContextGetter:
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY environment variable is not set. "
                "Please set it in your .env file or environment."
            )
        self.client = OpenAI(api_key=self.api_key)

    def take_photo(self, camera_index: int = 0) -> str | None:
        camera = cv2.VideoCapture(camera_index)
        if not camera.isOpened():
            raise Exception("Failed to open camera")

        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)

        ret, frame = camera.read()
        camera.release()

        if not ret:
            print("Error: Could not read frame from camera")
            return None

        if frame is None:
            print("Error: Frame is empty")
            return None

        save_dir = (
            Path(__file__).parent.parent.parent.parent
            / "brain"
            / "data"
            / "surroundings"
            / "images"
        )
        save_dir.mkdir(parents=True, exist_ok=True)

        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        filename = save_dir / f"{timestamp}.jpg"
        success = cv2.imwrite(str(filename), frame)

        if not success:
            print(f"Error: Could not save image to {filename}")
            return None

        return str(filename)

    def _encode_image(self, image_path: str) -> str:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")

    def _run_blocking(self) -> str | None:
        """Blocking context computation. Runs in a worker thread."""
        photo_path = self.take_photo()
        if photo_path is None:
            return None

        base64_image = self._encode_image(photo_path)

        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": SurroundingsContextGetterPrompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            },
                        },
                    ],
                }
            ],
        )

        content = response.choices[0].message.content
        if not content:
            print("Error: Empty response from OpenAI API")
            return None
        
        # Try to extract JSON from markdown code blocks if present
        content = content.strip()
        if content.startswith("```"):
            # Remove markdown code block markers
            lines = content.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines)
        
        try:
            result = json.loads(content)
        except json.JSONDecodeError as e:
            print(f"Error: Failed to parse JSON response: {e}")
            print(f"Response content: {content[:500]}")  # Print first 500 chars for debugging
            return None
        
        if "structured" not in result or "description" not in result:
            print("Error: Response missing required fields 'structured' or 'description'")
            print(f"Response keys: {list(result.keys())}")
            return None
        
        robot_state.state["environment"].update(result["structured"])
        robot_state.state["environment"]["description"] = result["description"]

        save_dir = (
            Path(__file__).parent.parent.parent.parent
            / "brain"
            / "data"
            / "surroundings"
            / "contexts"
        )
        save_dir.mkdir(parents=True, exist_ok=True)

        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        output_path = save_dir / f"{timestamp}.txt"
        with open(output_path, "w") as f:
            # Save the cleaned JSON content
            f.write(json.dumps(result, indent=2))

        return str(output_path)

    async def run(self) -> str | None:
        """Async wrapper that offloads blocking vision + OpenAI to a thread."""
        return await asyncio.to_thread(self._run_blocking)

    def __call__(self):
        return self.run()