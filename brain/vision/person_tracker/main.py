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

try:
    from insightface.app import FaceAnalysis as _InsightFaceApp
    _INSIGHTFACE_AVAILABLE = True
except ImportError:
    _INSIGHTFACE_AVAILABLE = False

try:
    import mediapipe as _mp
    _MP_FACE = _mp.solutions.face_detection  # only works on mediapipe < 0.10
    _MEDIAPIPE_AVAILABLE = True
except Exception:
    _MEDIAPIPE_AVAILABLE = False


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
        self._disable_pitch: bool = False
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
            self._disable_pitch = bool(self._tracking_cfg.get("disable_pitch", False))
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

    def _read_pitch_override(self) -> Optional[float]:
        """Read manual pitch angle from the enabled file (used when disable_pitch=True)."""
        try:
            data = json.loads(self._enabled_path.read_text())
            val = data.get("pitch_angle")
            if val is not None:
                return self._clamp_pitch(float(val))
        except Exception:
            pass
        return None

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
        disable_pitch = self._disable_pitch
        camera_index = int(cfg.get("camera_index", 0))
        gain_yaw = float(cfg.get("gain_yaw", 5.0))
        gain_pitch = float(cfg.get("gain_pitch", 3.0))
        invert_yaw = bool(cfg.get("invert_yaw", False))
        invert_pitch = bool(cfg.get("invert_pitch", False))
        dead_zone = float(cfg.get("dead_zone", 0.08))
        move_duration = float(cfg.get("move_duration", 0.3))
        max_fps = float(cfg.get("max_fps", 10.0))
        no_face_timeout = float(cfg.get("no_face_timeout", 3.0))
        face_smooth_alpha = float(cfg.get("face_smooth_alpha", 0.35))
        fast_smooth_alpha = float(cfg.get("fast_smooth_alpha", 0.8))
        confidence_max = int(cfg.get("confidence_max", 8))
        confidence_decay = int(cfg.get("confidence_decay", 2))
        frame_interval = 1.0 / max(1.0, max_fps)

        # EMA state for face centre position
        smooth_cx: float = 0.5
        smooth_cy: float = 0.5
        face_seen_once: bool = False
        face_confidence: int = 0  # 0..confidence_max; grows when face seen, decays when not

        insight_detector = None
        if _INSIGHTFACE_AVAILABLE:
            try:
                insight_detector = _InsightFaceApp(
                    name="buffalo_l", allowed_modules=["detection"]
                )
                insight_detector.prepare(ctx_id=0, det_size=(640, 640))
                print("[PersonTracker] Using InsightFace SCRFD detection (handles side profiles)")
            except Exception as e:
                print(f"[PersonTracker] InsightFace init failed, trying MediaPipe: {e}")

        mp_detector = None
        if insight_detector is None and _MEDIAPIPE_AVAILABLE:
            try:
                mp_detector = _MP_FACE.FaceDetection(model_selection=1, min_detection_confidence=0.5)
                print("[PersonTracker] Using MediaPipe face detection")
            except Exception as e:
                print(f"[PersonTracker] MediaPipe init failed, falling back to Haar: {e}")

        if insight_detector is None and mp_detector is None:
            print("[PersonTracker] Using Haar cascade face detection")
        haar_detector = cv2.CascadeClassifier(_HAAR_PATH)

        cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            print(f"[PersonTracker] Could not open camera {camera_index}")
            return

        try:
            while True:
                t_start = time.monotonic()

                # Always capture frame so Robot Studio feed works regardless of tracking state
                ret, frame = cap.read()
                if ret:
                    with self._frame_lock:
                        self._latest_frame = frame

                if not self._is_enabled():
                    # Release layer and drift back to neutral smoothly
                    needs_reset = abs(self._current_yaw - self._neutral_yaw) > 1.0 or \
                        (not disable_pitch and abs(self._current_pitch - self._neutral_pitch) > 1.0)
                    if needs_reset:
                        neutral_targets: dict = {"NeckYaw": self._neutral_yaw}
                        if not disable_pitch:
                            neutral_targets["NeckPitch"] = self._neutral_pitch
                        self._mixer.set_layer(
                            TRACKING_LAYER, TRACKING_PRIORITY, neutral_targets, duration=0.5,
                        )
                        time.sleep(0.55)
                        self._current_yaw = self._neutral_yaw
                        self._current_pitch = self._neutral_pitch

                    self._mixer.release_layer(TRACKING_LAYER, duration=0.1)
                    time.sleep(0.25)
                    continue

                if not ret:
                    time.sleep(0.1)
                    continue

                h, w = frame.shape[:2]
                if insight_detector is not None:
                    detected = insight_detector.get(frame)
                    faces = []
                    for face in detected:
                        x1, y1, x2, y2 = face.bbox.astype(int)
                        faces.append((x1, y1, x2 - x1, y2 - y1))
                elif mp_detector is not None:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    results = mp_detector.process(rgb)
                    faces = []
                    if results.detections:
                        for det in results.detections:
                            bb = det.location_data.relative_bounding_box
                            fx = int(max(0.0, bb.xmin) * w)
                            fy = int(max(0.0, bb.ymin) * h)
                            fw_px = int(bb.width * w)
                            fh_px = int(bb.height * h)
                            faces.append((fx, fy, fw_px, fh_px))
                else:
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    raw = haar_detector.detectMultiScale(
                        gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60),
                    )
                    faces = list(raw) if len(raw) > 0 else []

                # Read manual pitch override (only relevant when disable_pitch=True)
                pitch_override = self._read_pitch_override() if disable_pitch else None

                if len(faces) > 0:
                    # Build confidence — more consecutive detections = faster tracking
                    face_confidence = min(confidence_max, face_confidence + 1)
                    conf_ratio = face_confidence / confidence_max  # 0..1

                    # Track the largest face
                    x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
                    raw_cx = (x + fw / 2.0) / w  # normalised 0..1; 0.5 = centre
                    raw_cy = (y + fh / 2.0) / h

                    # EMA smoothing — snap on first detection; blend with confidence-scaled alpha
                    # (low confidence → gentle smoothing; full confidence → snappy/responsive)
                    if not face_seen_once:
                        smooth_cx, smooth_cy = raw_cx, raw_cy
                        face_seen_once = True
                    else:
                        effective_alpha = face_smooth_alpha + conf_ratio * (fast_smooth_alpha - face_smooth_alpha)
                        smooth_cx = effective_alpha * raw_cx + (1.0 - effective_alpha) * smooth_cx
                        smooth_cy = effective_alpha * raw_cy + (1.0 - effective_alpha) * smooth_cy

                    err_x = smooth_cx - 0.5   # positive → face is right of centre
                    err_y = smooth_cy - 0.5   # positive → face is below centre

                    self._last_face_time = time.monotonic()

                    dir_yaw = -1.0 if invert_yaw else 1.0

                    # Scale gains and speed with confidence (half gains at 0, full at max)
                    gain_scale = 0.5 + 0.5 * conf_ratio
                    effective_duration = move_duration * (1.0 - 0.5 * conf_ratio)

                    if abs(err_x) > dead_zone:
                        self._current_yaw = self._clamp_yaw(
                            self._current_yaw + err_x * gain_yaw * dir_yaw * gain_scale
                        )

                    targets: dict = {"NeckYaw": self._current_yaw}

                    if not disable_pitch:
                        dir_pitch = -1.0 if invert_pitch else 1.0
                        if abs(err_y) > dead_zone:
                            self._current_pitch = self._clamp_pitch(
                                self._current_pitch + err_y * gain_pitch * dir_pitch * gain_scale
                            )
                        targets["NeckPitch"] = self._current_pitch
                    elif pitch_override is not None:
                        targets["NeckPitch"] = pitch_override

                    self._mixer.set_layer(
                        TRACKING_LAYER, TRACKING_PRIORITY, targets, duration=effective_duration,
                    )
                else:
                    # Decay confidence slowly so brief occlusions don't reset it
                    face_confidence = max(0, face_confidence - confidence_decay)
                    if face_confidence == 0:
                        face_seen_once = False  # next detection will snap, not blend
                    # No face — drift back to neutral after timeout
                    since_last = time.monotonic() - self._last_face_time
                    if since_last > no_face_timeout:
                        needs_yaw = abs(self._current_yaw - self._neutral_yaw) > 1.0
                        needs_pitch = not disable_pitch and abs(self._current_pitch - self._neutral_pitch) > 1.0
                        if needs_yaw or needs_pitch:
                            self._current_yaw = self._neutral_yaw
                            neutral_targets = {"NeckYaw": self._neutral_yaw}
                            if not disable_pitch:
                                self._current_pitch = self._neutral_pitch
                                neutral_targets["NeckPitch"] = self._neutral_pitch
                            elif pitch_override is not None:
                                neutral_targets["NeckPitch"] = pitch_override
                            self._mixer.set_layer(
                                TRACKING_LAYER, TRACKING_PRIORITY, neutral_targets, duration=0.8,
                            )
                    elif disable_pitch and pitch_override is not None:
                        # Keep pitch at manual override even while waiting for a face
                        self._mixer.set_layer(
                            TRACKING_LAYER, TRACKING_PRIORITY,
                            {"NeckYaw": self._current_yaw, "NeckPitch": pitch_override},
                            duration=move_duration,
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
