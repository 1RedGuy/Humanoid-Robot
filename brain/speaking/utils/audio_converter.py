import subprocess
import os
import time
import uuid
from pathlib import Path


def mp3_to_wav_bytes(mp3_bytes: bytes) -> bytes:
    """
    Convert MP3 bytes to WAV bytes using ffmpeg.
    
    Args:
        mp3_bytes: MP3 audio data as bytes
        
    Returns:
        WAV audio data as bytes
        
    Raises:
        RuntimeError: If ffmpeg is not found or conversion fails
    """
    # Get temp directory in data folder
    from brain.config import PROJECT_ROOT
    temp_dir = PROJECT_ROOT / "brain" / "data" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Create temporary files with unique identifier
    unique_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    mp3_path = temp_dir / f"audio_{unique_id}.mp3"
    wav_path = temp_dir / f"audio_{unique_id}.wav"
    
    # Write MP3 bytes to file
    with open(mp3_path, 'wb') as mp3_file:
        mp3_file.write(mp3_bytes)
    
    try:
        # Convert MP3 to WAV using ffmpeg
        subprocess.run(
            ['ffmpeg', '-i', str(mp3_path), '-y', str(wav_path)],
            capture_output=True,
            check=True
        )
        
        # Read WAV file
        with open(wav_path, 'rb') as wav_file:
            wav_bytes = wav_file.read()
        
        return wav_bytes
        
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode() if e.stderr else "Unknown ffmpeg error"
        raise RuntimeError(f"FFmpeg conversion error: {error_msg}")
    except FileNotFoundError:
        raise RuntimeError(
            "ffmpeg not found. Install it: "
            "brew install ffmpeg (macOS) or apt-get install ffmpeg (Linux)"
        )
    finally:
        # Cleanup temp files
        try:
            if mp3_path.exists():
                mp3_path.unlink()
            if wav_path.exists():
                wav_path.unlink()
        except:
            pass

