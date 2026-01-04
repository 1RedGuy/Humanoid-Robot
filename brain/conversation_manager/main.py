import asyncio
import uuid
import time
import json
import wave
from pathlib import Path
from brain.config import PROJECT_ROOT
from brain.state import robot_state
from brain.audio.capture.main import AudioCapture
from brain.speaking.transcription.main import Transcription
from brain.speaking.main import Speaking

class ConversationManager:
    def __init__(self):
        self.audio_capture = AudioCapture()
        self.transcription = Transcription()
        self.speaking = Speaking()
        self.conversation_id = None
        self.save_dir = None

    def _run_blocking(self):
        """Blocking conversation flow. Runs in a worker thread."""
        self.conversation_start()
    
    def conversation_start(self):
        """Start a new conversation (blocking)."""
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

        self.conversation_loop()

    def conversation_loop(self):
        message_index = 0
        while True: 
            result = self.get_sentence()
            
            # No speech detected for 6+ seconds - end conversation
            # (User's turn to speak but they didn't speak)
            if not result:
                print("No speech detected for 6+ seconds - ending conversation")
                self.conversation_end()
                break
            
            # result is a tuple: (text, audio_data)
            text, audio_data = result
                
            # Save user audio file
            user_audio_path = self._save_user_audio(audio_data, message_index)
            
            # Add to conversation
            self.conversation_data["messages"].append({
                "role": "user",
                "content": text,
                "timestamp": time.time(),
                "audio_file": str(user_audio_path) if user_audio_path else None,
            })
            
            # Generate response, convert to audio, play, and save
            # speak() handles: generate_response → generate_audio → play_audio
            assistant_audio_path = self._get_assistant_audio_path(message_index)
            response_text, audio_bytes = self.speaking.speak(
                self.conversation_data, 
                save_path=assistant_audio_path
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

    def get_sentence(self):
        """
        Captures and transcribes audio.
        
        Returns:
            - tuple: (text, audio_data) if speech detected and transcribed successfully
            - None: If no speech detected for 6+ seconds (ends conversation)
        """
        audio_data = self.audio_capture.capture_sentence()
        
        # No speech detected for 6+ seconds - end conversation
        if audio_data is None:
            return None
        
        # Speech detected and captured
        # (Either 6 seconds of silence after speech ended it normally, 
        #  or 20s max_duration reached while still talking - process what we have)
        # Transcribe it and return both text and audio
        text = self.transcription.transcribe(audio_data)
        if text:
            return (text, audio_data)
        return None
    
    def _save_user_audio(self, audio_data: bytes, message_index: int) -> Path | None:
        """Save user audio to conversation folder."""
        if not audio_data:
            return None
        
        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"user_{message_index:03d}_{timestamp}.wav"
        audio_path = self.save_dir / filename
        
        # Save audio using AudioCapture's format
        with wave.open(str(audio_path), 'wb') as wf:
            wf.setnchannels(self.audio_capture.channels)
            wf.setsampwidth(self.audio_capture.audio.get_sample_size(self.audio_capture.format))
            wf.setframerate(self.audio_capture.rate)
            wf.writeframes(audio_data)
        
        return audio_path
    
    def _get_assistant_audio_path(self, message_index: int) -> Path:
        """Get the path where assistant audio should be saved."""
        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"assistant_{message_index:03d}_{timestamp}.mp3"
        return self.save_dir / filename


    async def run(self):
        """Async wrapper that offloads blocking conversation to a thread."""
        await asyncio.to_thread(self._run_blocking)

