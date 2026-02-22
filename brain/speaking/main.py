import base64
import os
import openai
from typing import Callable

from brain.config import speaking_model, thinking_model, voice_id, SpeakingPrompt
import time
from elevenlabs.client import ElevenLabs
from dotenv import load_dotenv
import pyaudio
import wave
from io import BytesIO
from pathlib import Path

from brain.speaking.utils.audio_converter import mp3_to_wav_bytes

load_dotenv()

_VOICE_STABILITY = 0.75
_VOICE_SIMILARITY_BOOST = 0.75


class Speaking:
    def __init__(self):
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        if not self.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY environment variable is not set. "
                "Please set it in your .env file or environment."
            )
        self.client = openai.OpenAI(api_key=self.openai_api_key)
        self.model = thinking_model

        self.elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY")
        if not self.elevenlabs_api_key:
            raise ValueError(
                "ELEVENLABS_API_KEY environment variable is not set. "
                "Please set it in your .env file or environment."
            )
        self.elevenlabs_client = ElevenLabs(api_key=self.elevenlabs_api_key)
        self.voice_id = voice_id
        self.speaking_model = speaking_model
        try:
            from elevenlabs import VoiceSettings
            self._voice_settings = VoiceSettings(
                stability=_VOICE_STABILITY,
                similarity_boost=_VOICE_SIMILARITY_BOOST,
            )
        except ImportError:
            self._voice_settings = None

    def speak(
        self,
        conversation_data: dict,
        save_path: Path | None = None,
        on_audio_ready: Callable | None = None,
    ) -> tuple[str, bytes]:
        """
        Generate response, convert to audio, and play it.

        Args:
            conversation_data: Conversation history and environment.
            save_path: Optional path to save the audio file.
            on_audio_ready: Optional callback ``(alignment_dict_or_None) -> ...``
                            invoked right before playback starts.

        Returns:
            (response_text, audio_bytes)
        """
        response_text = self.generate_response(conversation_data)

        audio_bytes, alignment = self.generate_audio(response_text)

        if save_path:
            with open(save_path, 'wb') as f:
                f.write(audio_bytes)

        if on_audio_ready:
            on_audio_ready(alignment)

        self.play_audio(audio_bytes)

        return response_text, audio_bytes

    def generate_response(self, conversation_data: dict):

        system_prompt = self._build_system_prompt(conversation_data["environment"])

        messages = [
            {"role": "system", "content": system_prompt},
        ]

        for message in conversation_data["messages"]:
            messages.append({
                "role": message["role"],
                "content": message["content"],
            })

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
        )

        return response.choices[0].message.content

    def generate_audio(self, text: str) -> tuple[bytes, dict | None]:
        """Generate TTS audio with character-level timing for lip sync.

        Returns:
            (mp3_bytes, alignment_dict) where alignment_dict has keys
            ``characters``, ``character_start_times_seconds``, and
            ``character_end_times_seconds``.  Returns ``(bytes, None)``
            on failure so playback still works without lip sync.
        """
        try:
            tts_kwargs = dict(
                voice_id=self.voice_id,
                text=text,
                model_id=self.speaking_model,
                output_format="mp3_44100_128",
            )
            if self._voice_settings is not None:
                tts_kwargs["voice_settings"] = self._voice_settings
            response = self.elevenlabs_client.text_to_speech.convert_with_timestamps(
                **tts_kwargs,
            )
            audio_bytes = base64.b64decode(response.audio_base_64)
            alignment = None
            if response.alignment is not None:
                alignment = {
                    "characters": list(response.alignment.characters),
                    "character_start_times_seconds": list(response.alignment.character_start_times_seconds),
                    "character_end_times_seconds": list(response.alignment.character_end_times_seconds),
                }
            return audio_bytes, alignment
        except Exception as e:
            print(f"[Speaking] convert_with_timestamps failed, falling back to convert: {e}")
            fallback_kwargs = dict(
                voice_id=self.voice_id,
                text=text,
                model_id=self.speaking_model,
                output_format="mp3_44100_128",
            )
            if self._voice_settings is not None:
                fallback_kwargs["voice_settings"] = self._voice_settings
            audio_generator = self.elevenlabs_client.text_to_speech.convert(
                **fallback_kwargs,
            )
            audio_bytes = b''.join(audio_generator)
            return audio_bytes, None

    def play_audio(self, audio_bytes: bytes):
        """
        Play MP3 audio bytes using pyaudio.
        
        Args:
            audio_bytes: MP3 audio data as bytes
        """
        # Convert MP3 to WAV
        wav_bytes = mp3_to_wav_bytes(audio_bytes)
        
        # Play using pyaudio
        p = pyaudio.PyAudio()
        
        try:
            # Open WAV from bytes
            wav_io = BytesIO(wav_bytes)
            with wave.open(wav_io, 'rb') as wf:
                stream = p.open(
                    format=p.get_format_from_width(wf.getsampwidth()),
                    channels=wf.getnchannels(),
                    rate=wf.getframerate(),
                    output=True
                )
                
                # Read and play audio in chunks
                data = wf.readframes(1024)
                while data:
                    stream.write(data)
                    data = wf.readframes(1024)
                
                stream.stop_stream()
                stream.close()
        finally:
            p.terminate()

    def _build_system_prompt(self, environment: dict):
        env_context = f"""
        - Location Type: {environment.get('location_type', 'unknown')}
        - Room Type: {environment.get('room_type', 'unknown')}
        - Lighting: {environment.get('lighting', 'unknown')}
        - Location Name: {environment.get('location_name', 'unknown')}
        - Notable Objects: {', '.join(environment.get('notable_objects', [])) or 'none'}
        - People Present: {environment.get('people_present', 'unknown')}
        - Activity Level: {environment.get('activity_level', 'unknown')}
        - Time of Day: {time.strftime("%H:%M:%S")}
        - Detailed Description: {environment.get('description', 'No description available')}
        """

        return f"""
        Instructions:
        {SpeakingPrompt}
        Current Surroundings Context:
        {env_context}
        """