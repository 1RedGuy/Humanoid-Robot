import os
import openai
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

    def speak(self, conversation_data: dict, save_path: Path | None = None) -> tuple[str, bytes]:
        """
        Generate response from conversation, convert to audio, and play it.
        This is the main "answer" method that handles the full flow.
        
        Args:
            conversation_data: Dictionary containing conversation history and environment
            save_path: Optional path to save the audio file (if None, audio is not saved)
            
        Returns:
            tuple: (response_text, audio_bytes) - the generated response and audio data
        """
        # Step 1: Generate text response from conversation
        response_text = self.generate_response(conversation_data)
        
        # Step 2: Convert text to audio
        audio_bytes = self.generate_audio(response_text)
        
        # Step 3: Save audio if path provided
        if save_path:
            with open(save_path, 'wb') as f:
                f.write(audio_bytes)
        
        # Step 4: Play audio
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

    def generate_audio(self, text: str):
        audio_generator = self.elevenlabs_client.text_to_speech.convert(
            voice_id=self.voice_id,
            text=text,
            model_id=self.speaking_model,
            output_format="mp3_44100_128"
        )
        audio_bytes = b''.join(audio_generator)
        return audio_bytes

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