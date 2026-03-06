"""
Person tracker — detects faces in the camera feed and moves the robot's
neck to follow the detected person.

Uses OpenCV Haar cascade face detection (built into ``opencv-python``; no
additional dependencies required).

Architecture
------------
Runs in a daemon background thread so it never blocks the asyncio event
loop.  Writes neck targets into the ServoMixer priority layer
``"person_tracking"`` at priority **4** — above the idle neck drift
(priority 3) but well below blink/wink (priority 10).

Enable / disable
----------------
The tracker reads ``person_tracking_enabled.json`` every loop iteration
to support hot-toggling from Robot Studio without restarting the brain.

    {"person_tracking_enabled": true}

When disabled, the neck is returned to neutral and the mixer layer is
released so the idle neck drift resumes.

Shared frame buffer
-------------------
The most recently captured frame is kept in ``_latest_frame`` (protected by
a threading.Lock).  ``SurroundingsContextGetter`` can call
``get_latest_frame()`` to reuse the same frame instead of opening the
camera a second time.

Configuration (servo_data.json → ``person_tracking`` key)
---------------------------------------------------------
    camera_index      : int    (default 0)
    gain_yaw          : float  proportional gain for yaw error (default 5.0)
    gain_pitch        : float  proportional gain for pitch error (default 3.0)
    invert_yaw        : bool   flip left/right correction (default false)
    invert_pitch      : bool   flip up/down correction (default false)
    dead_zone         : float  normalised dead zone fraction, 0-1 (default 0.08)
    move_duration     : float  servo interpolation duration in seconds (default 0.3)
    max_fps           : float  processing frame rate (default 10)
    no_face_timeout   : float  seconds after last detection to return neutral (default 3.0)
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from brain.movement.servo_mixer import ServoMixer

TRACKING_LAYER = "person_tracking"
TRACKING_PRIORITY = 4

_HAAR_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"


class PersonTracker:
    """Follow detected faces with the robot's head.

    Parameters
    ----------
    mixer :
        Shared priority servo mixer.
    config_path :
        Path to ``servo_data.json``.
    enabled_path :
        Path to the JSON toggle file
        (``brain/data/person_tracking_enabled.json``).
    """

    def __init__(
        self,
        mixer: ServoMixer,
        config_path: Path,
        enabled_path: Path,
    ):
        self._mixer = mixer
        self._config_path = config_path
        self._enabled_path = enabled_path

        # Servo limits / neutral — populated by _load_config
        self._tracking_cfg: dict = {}
        self._neutral_yaw: float = 180.0
        self._neutral_pitch: float = 200.0
        self._yaw_limits: tuple[float, float] = (0.0, 360.0)
        self._pitch_limits: tuple[float, float] = (0.0, 360.0)
        self._load_config()

        # Shared state
        self._latest_frame: Optional[np.ndarray] = None
        self._frame_lock = threading.Lock()
        self._current_yaw: float = self._neutral_yaw
        self._current_pitch: float = self._neutral_pitch
        self._last_face_time: float = 0.0

    # ── config ───────────────────────────────────────────────────────────

    def _load_config(self):
        try:
            with open(self._config_path, "r") as f:
                data = json.load(f)

            self._tracking_cfg = data.get("person_tracking", {})
            servos = data.get("servos", {})
            neutral_expr = data.get("expressions", {}).get("neutral", {})

            for name, attr_yaw, attr_pitch in (
                ("NeckYaw", True, False),
                ("NeckPitch", False, True),
            ):
                cfg = servos.get(name, {})
                neutral_val = (
                    cfg.get("neutral_angle")
                    or neutral_expr.get(name)
                    or (180.0 if name == "NeckYaw" else 200.0)
                )
                mn = float(cfg.get("min_angle", 0))
                mx = float(cfg.get("max_angle", 360))
                lo, hi = min(mn, mx), max(mn, mx)
                if attr_yaw:
                    self._neutral_yaw = float(neutral_val)
                    self._yaw_limits = (lo, hi)
                else:
                    self._neutral_pitch = float(neutral_val)
                    self._pitch_limits = (lo, hi)
        except Exception as e:
            print(f"[PersonTracker] Could not load config: {e}")

    # ── toggle ────────────────────────────────────────────────────────────

    def _is_enabled(self) -> bool:
        try:
            if not self._enabled_path.exists():
                return bool(self._tracking_cfg.get("enabled", False))
            data = json.loads(self._enabled_path.read_text())
            return bool(data.get("person_tracking_enabled", False))
        except Exception:
            return False

    # ── helpers ───────────────────────────────────────────────────────────

    def _clamp_yaw(self, v: float) -> float:
        return max(self._yaw_limits[0], min(self._yaw_limits[1], v))

    def _clamp_pitch(self, v: float) -> float:
        return max(self._pitch_limits[0], min(self._pitch_limits[1], v))

    def get_latest_frame(self) -> Optional[np.ndarray]:
        """Return a copy of the most recent camera frame.

        Called by ``SurroundingsContextGetter`` to share the camera instead
        of opening a second capture handle.
        """
        with self._frame_lock:
            return None if self._latest_frame is None else self._latest_frame.copy()

    # ── background thread ─────────────────────────────────────────────────

    def _tracking_thread(self):
        """Daemon thread: capture → detect → update neck layer."""
        cfg = self._tracking_cfg
        camera_index = int(cfg.get("camera_index", 0))
        gain_yaw = float(cfg.get("gain_yaw", 5.0))
        gain_pitch = float(cfg.get("gain_pitch", 3.0))
        invert_yaw = bool(cfg.get("invert_yaw", False))
        invert_pitch = bool(cfg.get("invert_pitch", False))
        dead_zone = float(cfg.get("dead_zone", 0.08))
        move_duration = float(cfg.get("move_duration", 0.3))
        max_fps = float(cfg.get("max_fps", 10.0))
        no_face_timeout = float(cfg.get("no_face_timeout", 3.0))
        frame_interval = 1.0 / max(1.0, max_fps)

        detector = cv2.CascadeClassifier(_HAAR_PATH)

        cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            print(f"[PersonTracker] Could not open camera {camera_index}")
            return

        try:
            while True:
                t_start = time.monotonic()

                if not self._is_enabled():
                    # Release layer and drift back to neutral smoothly
                    if abs(self._current_yaw - self._neutral_yaw) > 1.0 or \
                       abs(self._current_pitch - self._neutral_pitch) > 1.0:
                        self._mixer.set_layer(
                            TRACKING_LAYER, TRACKING_PRIORITY,
                            {"NeckYaw": self._neutral_yaw, "NeckPitch": self._neutral_pitch},
                            duration=0.5,
                        )
                        time.sleep(0.55)
                        self._current_yaw = self._neutral_yaw
                        self._current_pitch = self._neutral_pitch

                    self._mixer.release_layer(TRACKING_LAYER, duration=0.1)
                    time.sleep(0.25)
                    continue

                ret, frame = cap.read()
                if not ret:
                    time.sleep(0.1)
                    continue

                # Store frame for SurroundingsContextGetter
                with self._frame_lock:
                    self._latest_frame = frame

                h, w = frame.shape[:2]
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = detector.detectMultiScale(
                    gray,
                    scaleFactor=1.1,
                    minNeighbors=5,
                    minSize=(60, 60),
                )

                if len(faces) > 0:
                    # Track the largest face
                    x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
                    face_cx = (x + fw / 2.0) / w  # normalised 0..1; 0.5 = centre
                    face_cy = (y + fh / 2.0) / h

                    err_x = face_cx - 0.5   # positive → face is right of centre
                    err_y = face_cy - 0.5   # positive → face is below centre

                    self._last_face_time = time.monotonic()

                    dir_yaw = -1.0 if invert_yaw else 1.0
                    dir_pitch = -1.0 if invert_pitch else 1.0

                    if abs(err_x) > dead_zone:
                        self._current_yaw = self._clamp_yaw(
                            self._current_yaw + err_x * gain_yaw * dir_yaw
                        )
                    if abs(err_y) > dead_zone:
                        self._current_pitch = self._clamp_pitch(
                            self._current_pitch + err_y * gain_pitch * dir_pitch
                        )

                    self._mixer.set_layer(
                        TRACKING_LAYER, TRACKING_PRIORITY,
                        {"NeckYaw": self._current_yaw, "NeckPitch": self._current_pitch},
                        duration=move_duration,
                    )
                else:
                    # No face — drift back to neutral after timeout
                    since_last = time.monotonic() - self._last_face_time
                    if since_last > no_face_timeout:
                        if abs(self._current_yaw - self._neutral_yaw) > 1.0 or \
                           abs(self._current_pitch - self._neutral_pitch) > 1.0:
                            self._current_yaw = self._neutral_yaw
                            self._current_pitch = self._neutral_pitch
                            self._mixer.set_layer(
                                TRACKING_LAYER, TRACKING_PRIORITY,
                                {"NeckYaw": self._neutral_yaw, "NeckPitch": self._neutral_pitch},
                                duration=0.8,
                            )

                elapsed = time.monotonic() - t_start
                sleep_for = max(0.0, frame_interval - elapsed)
                if sleep_for > 0:
                    time.sleep(sleep_for)

        except Exception as e:
            print(f"[PersonTracker] tracking thread error: {e}")
        finally:
            cap.release()
            self._mixer.release_layer(TRACKING_LAYER, duration=0.1)

    # ── async entry point ────────────────────────────────────────────────

    async def run(self):
        """Start the background tracking thread and await forever.

        Designed to be added as a TaskGroup task in ``Brain.run()``.
        """
        thread = threading.Thread(
            target=self._tracking_thread,
            daemon=True,
            name="PersonTracker",
        )
        thread.start()

        # Keep coroutine alive; all real work happens in the thread
        while True:
            await asyncio.sleep(1.0)
