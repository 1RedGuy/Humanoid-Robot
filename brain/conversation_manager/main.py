import asyncio
import re
import threading
import uuid
import time
import json
import wave
from pathlib import Path
from typing import Callable, Optional

from brain.config import PROJECT_ROOT, transcription_language, EMOTION_TO_EXPRESSION, LLM_ACTIONS
from brain.state import robot_state
from brain.audio.capture.main import AudioCapture
from brain.speaking.transcription.main import Transcription
from brain.speaking.main import Speaking
from brain.movement.face_controller import FaceController
from brain.movement.lip_sync import LipSyncController

# ── tag parsing ───────────────────────────────────────────────────────────────

_TAG_PATTERN = re.compile(r'\[(\w+):(\w+)\]')


def _parse_tags(raw_text: str) -> tuple[str, str | None, str | None]:
    """Extract ``[EMOTION:X]`` and ``[ACTION:X]`` tags from the LLM response.

    Returns
    -------
    tuple[str, str | None, str | None]
        ``(clean_text, emotion, action)`` where *clean_text* has all tags
        stripped and is safe to pass to TTS.
    """
    emotion: str | None = None
    action: str | None = None
    for m in _TAG_PATTERN.finditer(raw_text):
        tag_type = m.group(1).upper()
        tag_value = m.group(2).upper()
        if tag_type == "EMOTION" and emotion is None:
            emotion = tag_value
        elif tag_type == "ACTION" and action is None:
            action = tag_value
    clean = _TAG_PATTERN.sub("", raw_text).strip()
    return clean, emotion, action


class ConversationManager:
    def __init__(
        self,
        face_controller: Optional[FaceController] = None,
        lip_sync: Optional[LipSyncController] = None,
        on_event: Optional[Callable] = None,
        neck_controller=None,  # optional NeckController for NOD / SHAKE actions
    ):
        self.audio_capture = AudioCapture()
        self.transcription = Transcription()
        self.speaking = Speaking()
        self.face_controller = face_controller
        self.lip_sync = lip_sync
        self._neck_controller = neck_controller
        self.conversation_id = None
        self.save_dir = None
        self._emit = on_event or (lambda t, d: None)

    # ── helpers ──

    def _set_face(self, expression: str, duration: float = 0.3):
        if self.face_controller:
            self.face_controller.set_expression(expression, duration=duration)

    def _set_activity(self, activity: str):
        robot_state.set_activity(activity)

    def _start_lip_sync(self, alignment: dict | None):
        if self.lip_sync and alignment:
            self.lip_sync.start(alignment)
            self._emit("lip_sync.started", {})

    def _stop_lip_sync(self):
        if self.lip_sync:
            self.lip_sync.stop()
            self._emit("lip_sync.stopped", {})

    def _execute_action(self, action: str):
        """Run a physical LLM action in a daemon thread (non-blocking)."""
        def _run():
            try:
                if action == "WINK_RIGHT" and self.face_controller:
                    self.face_controller.wink_right()
                elif action == "WINK_LEFT" and self.face_controller:
                    self.face_controller.wink_left()
                elif action == "NOD" and self._neck_controller:
                    self._neck_controller.nod()
                elif action == "SHAKE" and self._neck_controller:
                    self._neck_controller.shake()
            except Exception as e:
                print(f"[ConversationManager] action '{action}' error: {e}")

        threading.Thread(target=_run, daemon=True, name=f"llm_action_{action}").start()

    # ── conversation flow (blocking, runs in worker thread) ──

    def _run_blocking(self):
        self.conversation_start()

    def conversation_start(self):
        self.conversation_id = uuid.uuid4()
        self.save_dir = PROJECT_ROOT / "brain" / "data" / "conversations" / str(self.conversation_id)
        self.save_dir.mkdir(parents=True, exist_ok=True)

        self.conversation_data = {
            "conversation_id": str(self.conversation_id),
            "conversation_start_time": time.time(),
            "conversation_end_time": None,
            "environment": robot_state.get_environment(),
            "messages": [],
        }

        self._emit("conversation.started", {"conversation_id": str(self.conversation_id)})

        self._set_face("listening")
        self._set_activity("listening")

        self.conversation_loop()

    def conversation_loop(self):
        message_index = 0
        while True:
            self._set_face("listening")
            self._set_activity("listening")

            self._emit("audio.capture_start", {})
            result = self.get_sentence()
            self._emit("audio.capture_end", {"has_speech": result is not None})

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

            t_llm = time.monotonic()
            self._emit("llm.started", {})
            response_text = self.speaking.generate_response(self.conversation_data)
            llm_dur = time.monotonic() - t_llm

            # ── parse emotion / action tags ──────────────────────────────
            clean_text, emotion, action = _parse_tags(response_text)

            self._emit("llm.completed", {
                "text": clean_text,
                "duration_s": round(llm_dur, 2),
                "emotion": emotion,
                "action": action,
            })

            # Generate TTS from clean text (tags stripped — no tags in audio)
            t_tts = time.monotonic()
            self._emit("tts.started", {})
            audio_bytes, alignment = self.speaking.generate_audio(clean_text)
            tts_dur = time.monotonic() - t_tts
            self._emit("tts.completed", {
                "duration_s": round(tts_dur, 2),
                "has_alignment": alignment is not None,
            })

            if assistant_audio_path:
                with open(assistant_audio_path, 'wb') as f:
                    f.write(audio_bytes)

            # ── apply emotion expression ─────────────────────────────────
            # If the LLM specified an emotion, show that expression; otherwise
            # fall back to the generic "speaking" expression.
            if emotion:
                expr_name = EMOTION_TO_EXPRESSION.get(emotion)
                if expr_name:
                    self._set_face(expr_name, duration=0.3)
                else:
                    self._set_face("speaking")
            else:
                self._set_face("speaking")

            self._set_activity("speaking")

            # ── trigger physical action in background ────────────────────
            if action and action in LLM_ACTIONS:
                self._execute_action(action)

            self._start_lip_sync(alignment)

            self._emit("audio.playback_start", {})
            self.speaking.play_audio(audio_bytes)
            self._emit("audio.playback_end", {})

            self._stop_lip_sync()

            # Store clean text (no tags) in conversation history
            self.conversation_data["messages"].append({
                "role": "assistant",
                "content": clean_text,
                "timestamp": time.time(),
                "audio_file": str(assistant_audio_path) if assistant_audio_path else None,
            })

            message_index += 1

    def conversation_end(self):
        self.conversation_data["conversation_end_time"] = time.time()
        duration = self.conversation_data["conversation_end_time"] - self.conversation_data["conversation_start_time"]
        msg_count = len(self.conversation_data["messages"])

        try:
            with open(self.save_dir / "conversation.json", "w") as f:
                json.dump(self.conversation_data, f, indent=4, default=str)
        except Exception as e:
            print(f"[ConversationManager] Error saving conversation: {e}")
            self._emit("brain.error", {"error": f"Failed to save conversation: {e}"})

        self._emit("conversation.ended", {"duration_s": round(duration, 1), "message_count": msg_count})

        self._set_face("neutral")
        self._set_activity("idle")

    def get_sentence(self):
        audio_data = self.audio_capture.capture_sentence()

        if audio_data is None:
            return None

        self._emit("transcription.started", {})
        t0 = time.monotonic()
        text = self.transcription.transcribe(audio_data, language=transcription_language)
        dur = time.monotonic() - t0
        self._emit("transcription.completed", {"text": text or "", "duration_s": round(dur, 2)})

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
