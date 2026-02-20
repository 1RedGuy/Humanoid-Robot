import asyncio
import uuid
import time
import json
import wave
from pathlib import Path
from typing import Optional

from brain.config import PROJECT_ROOT
from brain.state import robot_state
from brain.audio.capture.main import AudioCapture
from brain.speaking.transcription.main import Transcription
from brain.speaking.main import Speaking
from brain.movement.face_controller import FaceController


class ConversationManager:
    def __init__(self, face_controller: Optional[FaceController] = None):
        self.audio_capture = AudioCapture()
        self.transcription = Transcription()
        self.speaking = Speaking()
        self.face_controller = face_controller
        self.conversation_id = None
        self.save_dir = None

    # ── helpers ──

    def _set_face(self, expression: str, duration: float = 0.3):
        if self.face_controller:
            self.face_controller.set_expression(expression, duration=duration)

    def _set_activity(self, activity: str):
        robot_state.set_activity(activity)

    # ── conversation flow (blocking, runs in worker thread) ──

    def _run_blocking(self):
        self.conversation_start()

    def conversation_start(self):
        self.conversation_id = uuid.uuid4()
        self.save_dir = PROJECT_ROOT / "brain" / "data" / "conversations" / str(self.conversation_id)
        self.save_dir.mkdir(parents=True, exist_ok=True)

        self.conversation_data = {
            "conversation_id": self.conversation_id,
            "conversation_start_time": time.time(),
            "conversation_end_time": None,
            "environment": robot_state.get_environment(),
            "messages": [],
        }

        self._set_face("listening")
        self._set_activity("listening")

        self.conversation_loop()

    def conversation_loop(self):
        message_index = 0
        while True:
            self._set_face("listening")
            self._set_activity("listening")

            result = self.get_sentence()

            if not result:
                print("No speech detected for 6+ seconds - ending conversation")
                self.conversation_end()
                break

            text, audio_data = result

            user_audio_path = self._save_user_audio(audio_data, message_index)

            self.conversation_data["messages"].append({
                "role": "user",
                "content": text,
                "timestamp": time.time(),
                "audio_file": str(user_audio_path) if user_audio_path else None,
            })

            self._set_face("thinking")
            self._set_activity("thinking")

            assistant_audio_path = self._get_assistant_audio_path(message_index)
            response_text, audio_bytes = self.speaking.speak(
                self.conversation_data,
                save_path=assistant_audio_path,
                on_audio_ready=lambda: (
                    self._set_face("speaking"),
                    self._set_activity("speaking"),
                ),
            )

            self.conversation_data["messages"].append({
                "role": "assistant",
                "content": response_text,
                "timestamp": time.time(),
                "audio_file": str(assistant_audio_path) if assistant_audio_path else None,
            })

            message_index += 1

    def conversation_end(self):
        self.conversation_data["conversation_end_time"] = time.time()

        with open(self.save_dir / "conversation.json", "w") as f:
            json.dump(self.conversation_data, f, indent=4)

        self._set_face("neutral")
        self._set_activity("idle")

    def get_sentence(self):
        audio_data = self.audio_capture.capture_sentence()

        if audio_data is None:
            return None

        text = self.transcription.transcribe(audio_data)
        if text:
            return (text, audio_data)
        return None

    def _save_user_audio(self, audio_data: bytes, message_index: int) -> Path | None:
        if not audio_data:
            return None

        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"user_{message_index:03d}_{timestamp}.wav"
        audio_path = self.save_dir / filename

        with wave.open(str(audio_path), 'wb') as wf:
            wf.setnchannels(self.audio_capture.channels)
            wf.setsampwidth(self.audio_capture.audio.get_sample_size(self.audio_capture.format))
            wf.setframerate(self.audio_capture.rate)
            wf.writeframes(audio_data)

        return audio_path

    def _get_assistant_audio_path(self, message_index: int) -> Path:
        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"assistant_{message_index:03d}_{timestamp}.mp3"
        return self.save_dir / filename

    async def run(self):
        """Async wrapper that offloads blocking conversation to a thread."""
        await asyncio.to_thread(self._run_blocking)
