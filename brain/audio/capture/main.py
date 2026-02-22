import time
import wave
from pathlib import Path
from typing import Optional, Union

import pyaudio
import numpy as np

class AudioCapture:
    def __init__(self, rate: int = 16000, chunk_size: int = 1024, channels: int = 1):
        """
        Args:
            rate: Sample rate in Hz (default 16000 for good speech recognition).
            chunk_size: Buffer size (default 1024).
            channels: Number of channels (default 1 for mono).
        """
        self.rate = rate
        self.chunk_size = chunk_size
        self.channels = channels
        self.format = pyaudio.paInt16
        self.audio = pyaudio.PyAudio()
        self.stream = None
    
    def _get_rms(self, data: bytes) -> float:
        """Calculate Root Mean Square amplitude of the audio chunk."""
        if not data:
            return 0
        
        audio_data = np.frombuffer(data, dtype=np.int16)
        if len(audio_data) == 0:
            return 0
        
        rms = np.sqrt(np.mean(audio_data.astype(np.float32)**2))
        return float(rms)

    def _calibrate_noise_floor(self, duration: float = 0.5) -> float:
        """
        Measure ambient noise to set a dynamic threshold.
        Assumes stream is already open.
        """
        frames = []
        num_chunks = int(self.rate * duration / self.chunk_size)
        
        self.stream.read(self.chunk_size, exception_on_overflow=False)
        
        for _ in range(num_chunks):
            data = self.stream.read(self.chunk_size, exception_on_overflow=False)
            frames.append(self._get_rms(data))
            
        avg_noise = sum(frames) / len(frames) if frames else 0

        return max(avg_noise * 1.5, 300.0)

    def capture_sentence(
        self, 
        threshold: Optional[float] = None, 
        silence_duration: float = 6.0,
        post_speech_silence: float = 1.5,
        max_duration: float = 20.0
    ) -> Optional[bytes]:
        """
        Captures audio until silence is detected after speech.
        
        Args:
            threshold: RMS threshold for speech detection. If None, auto-calibrates.
            silence_duration: Seconds with no speech at all before ending the
                conversation (user never started talking).
            post_speech_silence: Seconds of silence after speech to consider the
                utterance complete.
            max_duration: Maximum total duration to record in seconds.
            
        Returns:
            - Raw audio bytes (WAV format ready) if speech detected and captured
              successfully (ends when post_speech_silence of silence detected after
              speech, OR when max_duration reached while still talking)
            - None if no speech detected for silence_duration - ends conversation
        """
        
        if self.stream is None or not self.stream.is_active():
             self.stream = self.audio.open(
                format=self.format,
                channels=self.channels,
                rate=self.rate,
                input=True,
                frames_per_buffer=self.chunk_size
            )

        if threshold is None:
            threshold = self._calibrate_noise_floor()
            
        frames = []
        speech_started = False
        silence_start_time = None
        start_time = time.time()
        no_speech_start_time = start_time  # Track when we started waiting for speech
        
        pre_buffer_len = int(0.5 * self.rate / self.chunk_size)
        pre_buffer = []

        while True:
            elapsed = time.time() - start_time
            
            # Check if we've been waiting for speech for 6+ seconds without detecting any
            # This ends the conversation (user's turn but they didn't speak)
            if not speech_started:
                no_speech_elapsed = time.time() - no_speech_start_time
                if no_speech_elapsed >= silence_duration:
                    return None  # No speech for 6+ seconds - end conversation
            
            # max_duration (20s) reached while user is still talking
            # Process what we have and start answering
            if elapsed > max_duration:
                if speech_started:
                    return b''.join(frames)
                else:
                    return None
                
            try:
                data = self.stream.read(self.chunk_size, exception_on_overflow=False)
            except IOError:
                continue
                
            rms = self._get_rms(data)
            
            if not speech_started:
                pre_buffer.append(data)
                if len(pre_buffer) > pre_buffer_len:
                    pre_buffer.pop(0)
                
                if rms > threshold:
                    speech_started = True
                    frames.extend(pre_buffer)
                    frames.append(data)
                    silence_start_time = None
                    # Reset no_speech timer since we detected speech
                    no_speech_start_time = None
                else:
                    pass
            else:
                frames.append(data)
                
                if rms < threshold:
                    if silence_start_time is None:
                        silence_start_time = time.time()
                    elif time.time() - silence_start_time > post_speech_silence:
                        break
                else:
                    silence_start_time = None

        if not speech_started:
            return None
            
        return b''.join(frames)

    def save_to_file(self, audio_data: bytes):
        """Save raw audio data to a WAV file."""
        if not audio_data:
            return

        save_dir = Path(__file__).parent.parent.parent.parent / "brain" / "data" / "audio"
        save_dir.mkdir(parents=True, exist_ok=True)
        filename = save_dir / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}.wav"

        with wave.open(str(filename), 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(self.audio.get_sample_size(self.format))
            wf.setframerate(self.rate)
            wf.writeframes(audio_data)

    def close(self):
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
        self.audio.terminate()

    def __del__(self):
        self.close()
