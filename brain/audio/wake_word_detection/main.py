import asyncio
import os
from typing import Callable, Optional

import numpy as np
import pyaudio
import pvporcupine

from brain.config import WAKE_WORD_MODEL_PATH, WAKE_WORD_NAME


class WakeWordDetection:
    def __init__(self, on_event: Optional[Callable] = None):
        self._emit = on_event or (lambda t, d: None)
        self.audio = None
        self.stream = None
        self.porcupine = pvporcupine.create(
            access_key=os.getenv("PORCUPINE_API_KEY"),
            keyword_paths=[str(WAKE_WORD_MODEL_PATH)],
        )

    def _run_blocking(self) -> bool:
        self._emit("wake_word.listening", {})
        try:
            sample_rate = self.porcupine.sample_rate
            frame_length = self.porcupine.frame_length

            self.audio = pyaudio.PyAudio()
            self.stream = self.audio.open(
                rate=sample_rate,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=frame_length,
            )

            while True:
                try:
                    pcm = self.stream.read(frame_length, exception_on_overflow=False)
                    pcm_array = np.frombuffer(pcm, dtype=np.int16)

                    keyword_index = self.porcupine.process(pcm_array)

                    if keyword_index >= 0:
                        self._emit("wake_word.detected", {"keyword": WAKE_WORD_NAME})
                        return True

                except Exception as e:
                    print(f"Error processing audio frame: {e}")
                    continue

        except Exception as e:
            print(f"Error in wake word detection: {e}")
            return False
        finally:
            self._cleanup()

    async def run(self) -> bool:
        return await asyncio.to_thread(self._run_blocking)

    def _cleanup(self):
        if self.stream is not None:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception as e:
                print(f"Error closing stream: {e}")
            finally:
                self.stream = None

        if self.audio is not None:
            try:
                self.audio.terminate()
            except Exception as e:
                print(f"Error terminating audio: {e}")
            finally:
                self.audio = None

    def __call__(self):
        return self.run()

    def __del__(self):
        self._cleanup()
        if hasattr(self, "porcupine") and self.porcupine is not None:
            try:
                self.porcupine.delete()
            except Exception:
                pass