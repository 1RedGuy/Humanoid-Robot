#!/usr/bin/env python3
"""
XVF3800 Direction of Arrival (DOA) and Beam Steering Test Script
================================================================

This script tests the XVF3800 microphone array's ability to:
1. Detect the direction (DOA) of incoming speech
2. Steer the beam to that specific direction
3. Isolate audio from that direction while suppressing other sounds

Test Flow:
----------
Phase 1 - DOA Detection:
    - Records audio with free-running beam (auto-select mode)
    - Monitors DOA values and detects speech direction
    - Displays real-time direction feedback
    
Phase 2 - Beam Steering & Isolation:
    - Steers fixed beam to the detected direction
    - Enables gating to suppress audio from other directions
    - Records isolated audio for comparison
    
Phase 3 - Playback & Comparison:
    - Plays back both recordings for comparison
    - Optionally saves recordings for analysis

Microphone Positioning Tips:
---------------------------
- Distance: Optimal 0.6-3m from speaker, max ~5m for far-field
- Linear array: Mics spaced 33mm apart, best for frontal sound
- Square array: 66mm sides, provides 360° coverage
- Placement: Unobstructed line-of-sight to speaker
- Height: Align with expected speaker's mouth level
- Environment: Minimize reflective surfaces and echoes
- Note: At least 10cm spacing between some mics needed for low-frequency

Usage:
------
    python test_Xvf_audio.py [--duration SECONDS] [--output-dir PATH]

Dependencies:
-------------
    - pyaudio (for audio capture)
    - pyusb (for XVF3800 control)
    - numpy (for audio processing)
    - wave (for saving audio files)
"""

import sys
import os
import time
import wave
import math
import threading
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List

import numpy as np
import pyaudio

# Import the XVF3800 control module
from xvf_host import find as find_xvf_device, ReSpeaker


# =============================================================================
# CONSTANTS
# =============================================================================

# Audio recording settings
SAMPLE_RATE = 16000  # Hz - standard for speech recognition
CHANNELS = 2  # XVF3800 outputs stereo (processed left/right channels)
FORMAT = pyaudio.paInt16
CHUNK_SIZE = 1024

# XVF3800 device identification
XVF_VENDOR_ID = 0x2886
XVF_PRODUCT_ID = 0x001A

# DOA detection settings
DOA_POLL_INTERVAL = 0.05  # seconds between DOA polls
SPEECH_DETECTION_THRESHOLD = 3  # consecutive detections to confirm speech
DOA_STABILITY_THRESHOLD = 15  # degrees - max variance for stable DOA

# LED effect modes
LED_EFFECT_OFF = 0
LED_EFFECT_BREATH = 1
LED_EFFECT_RAINBOW = 2
LED_EFFECT_SINGLE_COLOR = 3
LED_EFFECT_DOA = 4
LED_EFFECT_RING = 5


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def degrees_to_radians(degrees: float) -> float:
    """Convert degrees to radians."""
    return degrees * math.pi / 180.0


def radians_to_degrees(radians: float) -> float:
    """Convert radians to degrees."""
    return radians * 180.0 / math.pi


def find_audio_device(device_name_part: str = "XVF3800") -> Optional[int]:
    """
    Find the XVF3800 audio device index.
    
    Args:
        device_name_part: Part of the device name to search for
        
    Returns:
        Device index if found, None otherwise
    """
    audio = pyaudio.PyAudio()
    device_index = None
    
    print("\n🔍 Searching for audio devices...")
    print("-" * 60)
    
    for i in range(audio.get_device_count()):
        dev_info = audio.get_device_info_by_index(i)
        name = dev_info.get('name', '')
        max_input_channels = dev_info.get('maxInputChannels', 0)
        
        # Show all input devices
        if max_input_channels > 0:
            marker = ""
            if device_name_part.lower() in name.lower() or "respeaker" in name.lower():
                device_index = i
                marker = " ✅ (SELECTED)"
            print(f"  [{i}] {name} (inputs: {max_input_channels}){marker}")
    
    print("-" * 60)
    audio.terminate()
    
    return device_index


def list_all_audio_devices():
    """List all available audio devices."""
    audio = pyaudio.PyAudio()
    
    print("\n" + "=" * 60)
    print("AVAILABLE AUDIO DEVICES")
    print("=" * 60)
    
    print("\n📥 INPUT DEVICES:")
    print("-" * 40)
    for i in range(audio.get_device_count()):
        dev_info = audio.get_device_info_by_index(i)
        if dev_info.get('maxInputChannels', 0) > 0:
            print(f"  [{i}] {dev_info['name']}")
            print(f"      Channels: {dev_info['maxInputChannels']}, "
                  f"Sample Rate: {int(dev_info['defaultSampleRate'])}Hz")
    
    print("\n📤 OUTPUT DEVICES:")
    print("-" * 40)
    for i in range(audio.get_device_count()):
        dev_info = audio.get_device_info_by_index(i)
        if dev_info.get('maxOutputChannels', 0) > 0:
            print(f"  [{i}] {dev_info['name']}")
            print(f"      Channels: {dev_info['maxOutputChannels']}, "
                  f"Sample Rate: {int(dev_info['defaultSampleRate'])}Hz")
    
    print("=" * 60 + "\n")
    audio.terminate()


def calculate_rms(audio_data: bytes) -> float:
    """Calculate RMS amplitude of audio chunk."""
    if not audio_data:
        return 0.0
    
    data = np.frombuffer(audio_data, dtype=np.int16)
    if len(data) == 0:
        return 0.0
    
    return float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))


def save_audio(audio_frames: List[bytes], filepath: Path, 
               sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS):
    """Save audio frames to a WAV file."""
    with wave.open(str(filepath), 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16-bit = 2 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(b''.join(audio_frames))
    print(f"💾 Audio saved to: {filepath}")


# =============================================================================
# XVF3800 CONTROLLER CLASS
# =============================================================================

class XVF3800Controller:
    """
    High-level controller for the XVF3800 microphone array.
    
    Provides easy access to DOA detection and beam steering functionality.
    """
    
    def __init__(self, vid: int = XVF_VENDOR_ID, pid: int = XVF_PRODUCT_ID):
        """Initialize connection to XVF3800 device."""
        self.device: Optional[ReSpeaker] = None
        self.vid = vid
        self.pid = pid
        self._connect()
    
    def _connect(self):
        """Connect to the XVF3800 device."""
        print("\n🔌 Connecting to XVF3800...")
        self.device = find_xvf_device(vid=self.vid, pid=self.pid)
        
        if not self.device:
            raise RuntimeError(
                "❌ XVF3800 device not found!\n"
                "   - Check USB connection\n"
                "   - Verify VID:PID (default: 0x2886:0x001A)\n"
                "   - Ensure no other application is using the device"
            )
        
        print("✅ XVF3800 connected successfully!")
        self._print_device_info()
    
    def _print_device_info(self):
        """Print device information."""
        try:
            version = self.device.read("VERSION")
            build_msg = self.device.read("BLD_MSG")
            print(f"   Version: {'.'.join(map(str, version))}")
            print(f"   Build: {build_msg}")
        except Exception as e:
            print(f"   (Could not read device info: {e})")
    
    def get_doa(self) -> Tuple[int, bool]:
        """
        Get current Direction of Arrival (DOA) information.
        
        Returns:
            Tuple of (azimuth_degrees, speech_detected)
            - azimuth_degrees: 0-359, where 0 is front, 90 is right, etc.
            - speech_detected: True if speech is currently detected
        """
        result = self.device.read("DOA_VALUE")
        azimuth = result[0]  # 0-359 degrees
        speech_detected = result[1] == 1
        return azimuth, speech_detected
    
    def get_beam_azimuths(self) -> Tuple[float, float, float, float]:
        """
        Get azimuth values for all beams in radians.
        
        Returns:
            Tuple of (beam1, beam2, free_running, auto_select) azimuths
        """
        result = self.device.read("AEC_AZIMUTH_VALUES")
        return result
    
    def get_speech_energy(self) -> Tuple[float, float, float, float]:
        """
        Get speech energy levels for all beams.
        
        Returns:
            Tuple of energy values for (beam1, beam2, free_running, auto_select)
            Values > 0 indicate speech, higher = louder/closer
        """
        result = self.device.read("AEC_SPENERGY_VALUES")
        return result
    
    def get_selected_azimuths(self) -> Tuple[float, float]:
        """
        Get processed DOA azimuths determined by beam selection.
        
        Returns:
            Tuple of (processed_doa, auto_select_doa) in radians
            - processed_doa: DOA using speech energy (NaN if no speech)
            - auto_select_doa: DOA of auto-select beam
        """
        result = self.device.read("AUDIO_MGR_SELECTED_AZIMUTHS")
        return result
    
    def enable_fixed_beam_mode(self, enable: bool = True):
        """
        Enable or disable fixed (focused) beam mode.
        
        When enabled, beams are steered to specific directions set by
        set_fixed_beam_direction(). When disabled, beams auto-select.
        
        Args:
            enable: True to enable fixed beam mode, False for auto-select
        """
        value = 1 if enable else 0
        self.device.write("AEC_FIXEDBEAMSONOFF", [value])
        mode = "FIXED" if enable else "AUTO-SELECT"
        print(f"🎯 Beam mode: {mode}")
    
    def set_fixed_beam_direction(self, azimuth_deg: float, elevation_deg: float = 0.0,
                                  beam_index: int = 0):
        """
        Set the direction for fixed beam(s).
        
        Args:
            azimuth_deg: Horizontal angle in degrees (0=front, 90=right, 180=back, 270=left)
            elevation_deg: Vertical angle in degrees (0=horizontal, positive=up)
            beam_index: Which beam to set (0=both, 1=beam1 only, 2=beam2 only)
        """
        azimuth_rad = degrees_to_radians(azimuth_deg)
        elevation_rad = degrees_to_radians(elevation_deg)
        
        # Get current values
        try:
            current_az = list(self.device.read("AEC_FIXEDBEAMSAZIMUTH_VALUES"))
            current_el = list(self.device.read("AEC_FIXEDBEAMSELEVATION_VALUES"))
        except:
            current_az = [0.0, 0.0]
            current_el = [0.0, 0.0]
        
        if beam_index == 0:
            # Set both beams to the same direction
            new_az = [azimuth_rad, azimuth_rad]
            new_el = [elevation_rad, elevation_rad]
        elif beam_index == 1:
            new_az = [azimuth_rad, current_az[1]]
            new_el = [elevation_rad, current_el[1]]
        else:
            new_az = [current_az[0], azimuth_rad]
            new_el = [current_el[0], elevation_rad]
        
        self.device.write("AEC_FIXEDBEAMSAZIMUTH_VALUES", new_az)
        self.device.write("AEC_FIXEDBEAMSELEVATION_VALUES", new_el)
        
        print(f"📐 Fixed beam direction set to: {azimuth_deg:.1f}° azimuth, {elevation_deg:.1f}° elevation")
    
    def enable_beam_gating(self, enable: bool = True):
        """
        Enable or disable beam gating.
        
        When enabled, inactive beams are silenced based on speech energy.
        This helps isolate audio from the selected direction.
        
        Args:
            enable: True to enable gating, False to disable
        """
        value = 1 if enable else 0
        self.device.write("AEC_FIXEDBEAMSGATING", [value])
        state = "ENABLED" if enable else "DISABLED"
        print(f"🔇 Beam gating: {state}")
    
    def set_led_effect(self, effect: int):
        """
        Set the LED ring effect.
        
        Args:
            effect: LED effect mode (0=off, 1=breath, 2=rainbow, 3=single, 4=DOA, 5=ring)
        """
        self.device.write("LED_EFFECT", [effect])
        effects = {0: "OFF", 1: "BREATH", 2: "RAINBOW", 3: "SINGLE", 4: "DOA", 5: "RING"}
        print(f"💡 LED effect: {effects.get(effect, 'UNKNOWN')}")
    
    def close(self):
        """Close the device connection."""
        if self.device:
            self.device.close()
            self.device = None
            print("🔌 XVF3800 disconnected")


# =============================================================================
# AUDIO RECORDER CLASS
# =============================================================================

class AudioRecorder:
    """
    Audio recorder for XVF3800 device.
    """
    
    def __init__(self, device_index: Optional[int] = None,
                 sample_rate: int = SAMPLE_RATE, 
                 channels: int = CHANNELS,
                 chunk_size: int = CHUNK_SIZE):
        """
        Initialize audio recorder.
        
        Args:
            device_index: Audio device index (None for default)
            sample_rate: Sample rate in Hz
            channels: Number of audio channels
            chunk_size: Size of audio chunks
        """
        self.device_index = device_index
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self.format = FORMAT
        
        self.audio = pyaudio.PyAudio()
        self.stream = None
        self.frames: List[bytes] = []
        self.is_recording = False
        self._record_thread: Optional[threading.Thread] = None
    
    def start_recording(self):
        """Start recording audio in a background thread."""
        if self.is_recording:
            return
        
        self.frames = []
        self.is_recording = True
        
        self.stream = self.audio.open(
            format=self.format,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            input_device_index=self.device_index,
            frames_per_buffer=self.chunk_size
        )
        
        self._record_thread = threading.Thread(target=self._record_loop)
        self._record_thread.start()
        print("🔴 Recording started...")
    
    def _record_loop(self):
        """Background recording loop."""
        while self.is_recording:
            try:
                data = self.stream.read(self.chunk_size, exception_on_overflow=False)
                self.frames.append(data)
            except Exception as e:
                if self.is_recording:
                    print(f"⚠️ Recording error: {e}")
                break
    
    def stop_recording(self) -> List[bytes]:
        """
        Stop recording and return audio frames.
        
        Returns:
            List of recorded audio frames
        """
        self.is_recording = False
        
        if self._record_thread:
            self._record_thread.join(timeout=1.0)
        
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
        
        print(f"⏹️ Recording stopped. Captured {len(self.frames)} frames "
              f"({len(self.frames) * self.chunk_size / self.sample_rate:.1f}s)")
        
        return self.frames
    
    def record_for_duration(self, duration: float) -> List[bytes]:
        """
        Record audio for a specific duration.
        
        Args:
            duration: Recording duration in seconds
            
        Returns:
            List of recorded audio frames
        """
        self.start_recording()
        time.sleep(duration)
        return self.stop_recording()
    
    def close(self):
        """Clean up audio resources."""
        if self.is_recording:
            self.stop_recording()
        self.audio.terminate()


# =============================================================================
# DOA MONITOR CLASS
# =============================================================================

class DOAMonitor:
    """
    Monitors DOA values and detects stable speech directions.
    """
    
    def __init__(self, controller: XVF3800Controller):
        """
        Initialize DOA monitor.
        
        Args:
            controller: XVF3800 controller instance
        """
        self.controller = controller
        self.doa_history: List[int] = []
        self.speech_history: List[bool] = []
        self.is_monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None
        self.detected_direction: Optional[int] = None
    
    def start_monitoring(self):
        """Start monitoring DOA in background."""
        if self.is_monitoring:
            return
        
        self.is_monitoring = True
        self.doa_history = []
        self.speech_history = []
        self.detected_direction = None
        
        self._monitor_thread = threading.Thread(target=self._monitor_loop)
        self._monitor_thread.start()
    
    def _monitor_loop(self):
        """Background monitoring loop."""
        while self.is_monitoring:
            try:
                doa, speech = self.controller.get_doa()
                self.doa_history.append(doa)
                self.speech_history.append(speech)
                
                # Keep only last 100 samples
                if len(self.doa_history) > 100:
                    self.doa_history.pop(0)
                    self.speech_history.pop(0)
                
                # Check for stable direction when speech detected
                if speech and len(self.doa_history) >= SPEECH_DETECTION_THRESHOLD:
                    recent_doa = self.doa_history[-SPEECH_DETECTION_THRESHOLD:]
                    recent_speech = self.speech_history[-SPEECH_DETECTION_THRESHOLD:]
                    
                    if all(recent_speech):
                        # Calculate variance in recent DOA values
                        doa_variance = max(recent_doa) - min(recent_doa)
                        if doa_variance <= DOA_STABILITY_THRESHOLD:
                            self.detected_direction = int(np.mean(recent_doa))
                
                time.sleep(DOA_POLL_INTERVAL)
                
            except Exception as e:
                if self.is_monitoring:
                    print(f"⚠️ DOA monitoring error: {e}")
                time.sleep(DOA_POLL_INTERVAL)
    
    def stop_monitoring(self) -> Optional[int]:
        """
        Stop monitoring and return detected direction.
        
        Returns:
            Detected direction in degrees, or None if no stable direction found
        """
        self.is_monitoring = False
        
        if self._monitor_thread:
            self._monitor_thread.join(timeout=1.0)
        
        return self.detected_direction
    
    def get_current_doa(self) -> Tuple[int, bool]:
        """Get current DOA and speech status."""
        return self.controller.get_doa()


# =============================================================================
# TEST FUNCTIONS
# =============================================================================

def run_doa_detection_test(controller: XVF3800Controller, 
                           recorder: AudioRecorder,
                           duration: float = 10.0) -> Optional[int]:
    """
    Phase 1: Detect Direction of Arrival
    
    Records audio while monitoring DOA and displays real-time feedback.
    
    Args:
        controller: XVF3800 controller
        recorder: Audio recorder
        duration: Maximum duration to wait for speech
        
    Returns:
        Detected direction in degrees, or None if not detected
    """
    print("\n" + "=" * 60)
    print("PHASE 1: DIRECTION OF ARRIVAL DETECTION")
    print("=" * 60)
    print("\n📢 Please speak towards the microphone array.")
    print("   The system will detect the direction of your voice.\n")
    
    # Enable DOA LED effect for visual feedback
    controller.set_led_effect(LED_EFFECT_DOA)
    
    # Ensure auto-select mode (not fixed beam)
    controller.enable_fixed_beam_mode(False)
    
    # Start monitoring
    monitor = DOAMonitor(controller)
    monitor.start_monitoring()
    
    # Start recording
    recorder.start_recording()
    
    # Monitor for speech direction
    start_time = time.time()
    last_print_time = 0
    detected_direction = None
    
    print("\n🎤 Listening for speech...")
    print("-" * 40)
    
    try:
        while time.time() - start_time < duration:
            doa, speech = monitor.get_current_doa()
            
            # Print status every 0.2 seconds
            current_time = time.time()
            if current_time - last_print_time >= 0.2:
                speech_indicator = "🗣️ SPEECH" if speech else "   quiet"
                direction_arrow = get_direction_arrow(doa)
                energy = controller.get_speech_energy()
                energy_bar = get_energy_bar(max(energy))
                
                print(f"\r   DOA: {doa:3d}° {direction_arrow} | {speech_indicator} | Energy: {energy_bar}", 
                      end="", flush=True)
                last_print_time = current_time
            
            # Check if we have a stable detection
            if monitor.detected_direction is not None:
                detected_direction = monitor.detected_direction
                print(f"\n\n✅ Stable direction detected: {detected_direction}°")
                break
            
            time.sleep(0.05)
        
        print("\n" + "-" * 40)
        
    finally:
        monitor.stop_monitoring()
        frames = recorder.stop_recording()
    
    if detected_direction is None:
        print("⚠️ No stable speech direction detected.")
        print("   Try speaking louder or more continuously.")
    
    return detected_direction, frames


def run_beam_isolation_test(controller: XVF3800Controller,
                            recorder: AudioRecorder,
                            target_direction: int,
                            duration: float = 10.0) -> List[bytes]:
    """
    Phase 2: Beam Steering and Audio Isolation
    
    Steers the beam to the target direction and records isolated audio.
    
    Args:
        controller: XVF3800 controller
        recorder: Audio recorder
        target_direction: Target direction in degrees
        duration: Recording duration
        
    Returns:
        Recorded audio frames
    """
    print("\n" + "=" * 60)
    print("PHASE 2: BEAM STEERING AND AUDIO ISOLATION")
    print("=" * 60)
    print(f"\n🎯 Target direction: {target_direction}°")
    print("\n📢 The beam is now locked to your position.")
    print("   Noise from other directions should be suppressed.")
    print("   Try having someone speak from a different direction.\n")
    
    # Configure fixed beam mode
    controller.enable_fixed_beam_mode(True)
    controller.set_fixed_beam_direction(target_direction)
    controller.enable_beam_gating(True)
    
    # Keep DOA LED for visual reference
    controller.set_led_effect(LED_EFFECT_DOA)
    
    input("Press ENTER to start recording with beam isolation...\n")
    
    print(f"🔴 Recording for {duration} seconds...")
    print("-" * 40)
    
    # Record with beam steering
    recorder.start_recording()
    
    start_time = time.time()
    last_print_time = 0
    
    try:
        while time.time() - start_time < duration:
            doa, speech = controller.get_doa()
            
            current_time = time.time()
            if current_time - last_print_time >= 0.3:
                elapsed = current_time - start_time
                remaining = duration - elapsed
                speech_indicator = "🗣️ SPEECH" if speech else "   quiet"
                direction_arrow = get_direction_arrow(doa)
                
                print(f"\r   [{elapsed:4.1f}s/{duration:.0f}s] DOA: {doa:3d}° {direction_arrow} | "
                      f"{speech_indicator} | Target: {target_direction}°", 
                      end="", flush=True)
                last_print_time = current_time
            
            time.sleep(0.05)
        
        print("\n" + "-" * 40)
        
    finally:
        frames = recorder.stop_recording()
    
    # Reset to auto-select mode
    controller.enable_fixed_beam_mode(False)
    controller.enable_beam_gating(False)
    
    return frames


def get_direction_arrow(doa: int) -> str:
    """Get an arrow indicating the direction."""
    # Normalize to 0-360
    doa = doa % 360
    
    # 8-direction arrows
    if doa >= 337.5 or doa < 22.5:
        return "↑ (front)"
    elif doa < 67.5:
        return "↗ (front-right)"
    elif doa < 112.5:
        return "→ (right)"
    elif doa < 157.5:
        return "↘ (back-right)"
    elif doa < 202.5:
        return "↓ (back)"
    elif doa < 247.5:
        return "↙ (back-left)"
    elif doa < 292.5:
        return "← (left)"
    else:
        return "↖ (front-left)"


def get_energy_bar(energy: float, max_energy: float = 1.0, width: int = 10) -> str:
    """Get a visual energy bar."""
    if energy <= 0:
        return "[" + " " * width + "]"
    
    normalized = min(energy / max_energy, 1.0)
    filled = int(normalized * width)
    return "[" + "█" * filled + " " * (width - filled) + "]"


# =============================================================================
# MAIN TEST RUNNER
# =============================================================================

def main():
    """Main test function."""
    parser = argparse.ArgumentParser(
        description="XVF3800 DOA Detection and Beam Isolation Test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python test_Xvf_audio.py                    # Run with defaults
    python test_Xvf_audio.py --duration 15      # Record for 15 seconds
    python test_Xvf_audio.py --list-devices     # List audio devices only
    python test_Xvf_audio.py --output-dir ./recordings  # Custom output directory

Microphone Positioning Tips:
    - Optimal distance: 0.6-3 meters from speaker
    - Maximum effective range: ~5 meters
    - Linear array: Best for frontal sound capture
    - Square array: Provides 360° coverage
    - Keep line-of-sight clear between speaker and microphone
    - Minimize reflective surfaces in the room
        """
    )
    
    parser.add_argument("--duration", type=float, default=10.0,
                        help="Recording duration in seconds (default: 10)")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Directory to save recordings (default: brain/data/audio/tests)")
    parser.add_argument("--list-devices", action="store_true",
                        help="List all audio devices and exit")
    parser.add_argument("--device-index", type=int, default=None,
                        help="Audio device index to use (auto-detect if not specified)")
    parser.add_argument("--skip-isolation", action="store_true",
                        help="Skip phase 2 (isolation test)")
    parser.add_argument("--vid", type=lambda x: int(x, 0), default=XVF_VENDOR_ID,
                        help=f"USB Vendor ID (default: 0x{XVF_VENDOR_ID:04X})")
    parser.add_argument("--pid", type=lambda x: int(x, 0), default=XVF_PRODUCT_ID,
                        help=f"USB Product ID (default: 0x{XVF_PRODUCT_ID:04X})")
    
    args = parser.parse_args()
    
    # List devices and exit if requested
    if args.list_devices:
        list_all_audio_devices()
        return 0
    
    # Setup output directory
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = Path(__file__).parent.parent.parent / "data" / "audio" / "tests"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print("\n" + "=" * 60)
    print("XVF3800 DIRECTION OF ARRIVAL & BEAM ISOLATION TEST")
    print("=" * 60)
    print(f"\nConfiguration:")
    print(f"  - Recording duration: {args.duration}s")
    print(f"  - Output directory: {output_dir}")
    print(f"  - Device VID:PID: 0x{args.vid:04X}:0x{args.pid:04X}")
    
    controller = None
    recorder = None
    
    try:
        # Find audio device
        device_index = args.device_index
        if device_index is None:
            device_index = find_audio_device()
            if device_index is None:
                print("\n⚠️ No XVF3800 audio device found automatically.")
                print("   Use --list-devices to see available devices,")
                print("   then specify with --device-index")
                list_all_audio_devices()
                # Try to use default device
                print("\n💡 Attempting to use default input device...")
                device_index = None
        
        # Initialize controller and recorder
        controller = XVF3800Controller(vid=args.vid, pid=args.pid)
        recorder = AudioRecorder(device_index=device_index)
        
        # =================================================================
        # PHASE 1: DOA Detection
        # =================================================================
        
        input("\n📢 Press ENTER to start DOA detection phase...\n")
        
        detected_direction, phase1_frames = run_doa_detection_test(
            controller, recorder, duration=args.duration
        )
        
        # Save phase 1 recording
        if phase1_frames:
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            phase1_file = output_dir / f"phase1_doa_detection_{timestamp}.wav"
            save_audio(phase1_frames, phase1_file)
        
        # =================================================================
        # PHASE 2: Beam Isolation (if direction was detected)
        # =================================================================
        
        if detected_direction is not None and not args.skip_isolation:
            phase2_frames = run_beam_isolation_test(
                controller, recorder, detected_direction, duration=args.duration
            )
            
            # Save phase 2 recording
            if phase2_frames:
                phase2_file = output_dir / f"phase2_isolated_{detected_direction}deg_{timestamp}.wav"
                save_audio(phase2_frames, phase2_file)
        elif detected_direction is None:
            print("\n⚠️ Skipping Phase 2 - no direction was detected in Phase 1")
        else:
            print("\n⏩ Skipping Phase 2 as requested")
        
        # =================================================================
        # Summary
        # =================================================================
        
        print("\n" + "=" * 60)
        print("TEST COMPLETE")
        print("=" * 60)
        print(f"\n📁 Recordings saved to: {output_dir}")
        
        if detected_direction is not None:
            print(f"🎯 Detected speaker direction: {detected_direction}°")
            print(f"\n💡 Tips for verification:")
            print(f"   1. Play phase1 recording - should hear audio from all directions")
            print(f"   2. Play phase2 recording - should primarily hear audio from {detected_direction}°")
            print(f"   3. Background noise and speech from other directions should be reduced")
        
        print("\n" + "=" * 60)
        
    except KeyboardInterrupt:
        print("\n\n⚠️ Test interrupted by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        # Cleanup
        if recorder:
            recorder.close()
        if controller:
            controller.set_led_effect(LED_EFFECT_OFF)
            controller.close()
    
    return 0


# =============================================================================
# INTERACTIVE DOA MONITOR (Bonus utility)
# =============================================================================

def interactive_doa_monitor():
    """
    Interactive DOA monitoring utility.
    Continuously displays DOA information until user presses Ctrl+C.
    """
    print("\n" + "=" * 60)
    print("INTERACTIVE DOA MONITOR")
    print("=" * 60)
    print("\nPress Ctrl+C to stop\n")
    
    controller = None
    
    try:
        controller = XVF3800Controller()
        controller.set_led_effect(LED_EFFECT_DOA)
        controller.enable_fixed_beam_mode(False)
        
        print("Monitoring DOA values...")
        print("-" * 60)
        
        while True:
            doa, speech = controller.get_doa()
            energy = controller.get_speech_energy()
            azimuths = controller.get_beam_azimuths()
            
            speech_indicator = "🗣️ SPEECH" if speech else "   quiet"
            direction_arrow = get_direction_arrow(doa)
            energy_bar = get_energy_bar(max(energy))
            
            # Convert azimuths to degrees for display
            az_degrees = [radians_to_degrees(a) for a in azimuths]
            
            print(f"\r  DOA: {doa:3d}° {direction_arrow:20s} | {speech_indicator} | "
                  f"Energy: {energy_bar} | "
                  f"Beams: [{az_degrees[0]:.0f}°, {az_degrees[1]:.0f}°, {az_degrees[2]:.0f}°, {az_degrees[3]:.0f}°]",
                  end="", flush=True)
            
            time.sleep(0.1)
            
    except KeyboardInterrupt:
        print("\n\n👋 Monitoring stopped")
    finally:
        if controller:
            controller.set_led_effect(LED_EFFECT_OFF)
            controller.close()


if __name__ == "__main__":
    # Check if interactive mode requested
    if len(sys.argv) > 1 and sys.argv[1] == "--monitor":
        interactive_doa_monitor()
    else:
        sys.exit(main())
