import os
import io
import wave
import openai
from dotenv import load_dotenv

load_dotenv()

class Transcription:
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY environment variable is not set. "
                "Please set it in your .env file or environment."
            )
        self.client = openai.OpenAI(api_key=self.api_key)

    def transcribe(self, audio_data: bytes, language: str = "bg") -> str:
        if not audio_data:
            return ""
            
        audio_buffer = io.BytesIO()
        audio_buffer.name = "speech.wav"
        
        with wave.open(audio_buffer, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(audio_data)
            
        audio_buffer.seek(0)

        try:
            transcript = self.client.audio.transcriptions.create(
                model="whisper-1", 
                file=audio_buffer,
                language=language
            )
            return transcript.text
        except Exception as e:
            print(f"Transcription error: {e}")
            return ""
