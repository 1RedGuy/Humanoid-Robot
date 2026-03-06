import asyncio
import json
import os
import random
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from .serial_client import SerialClient
from .config_manager import ConfigManager
from .event_bus import EventBus
from .brain_runner import BrainRunner

STATIC_DIR = Path(__file__).parent / "static"
PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "esp32" / "servo_data.json"
IDLE_ENABLED_PATH = PROJECT_ROOT / "brain" / "data" / "idle_enabled.json"
PERSON_TRACKING_PATH = PROJECT_ROOT / "brain" / "data" / "person_tracking_enabled.json"


def create_app(
    config_path: Optional[str] = None,
    serial_port: Optional[str] = None,
) -> FastAPI:
    app = FastAPI(title="Robot Studio - Humanoid Robot")

    cfg = ConfigManager(config_path or DEFAULT_CONFIG)
    serial = SerialClient(port=serial_port)
    event_bus = EventBus()
    brain_runner = BrainRunner()

    positions: Dict[str, float] = {}
    idle_running = {"value": False}  # mutable so closure can set it
    current_mode = {"value": "manual"}  # "manual" or "auto"
    ws_clients: List[WebSocket] = []

    def _init_positions():
        positions.clear()
        for s in cfg.get_servo_list():
            positions[s["name"]] = cfg.calibrate_angle

    _init_positions()

    def _servo_response():
        grouped = cfg.get_grouped_servo_list()
        for group_servos in grouped.values():
            for s in group_servos:
                s["current_angle"] = positions.get(s["name"], cfg.calibrate_angle)
        return {
            "groups": grouped,
            "calibrate_angle": cfg.calibrate_angle,
            "linked_controls": cfg.get_linked_controls(),
        }

    def _reject_if_idle():
        """Raise 409 if idle is on (user must turn Idle off to control servos)."""
        if idle_running["value"]:
            raise HTTPException(
                status_code=409,
                detail="Idle is on. Turn Idle off to control servos or expressions.",
            )

    # --- Models -----------------------------------------------------------

    class MoveBody(BaseModel):
        angle: float
        duration: float = 0.3

    class MultiMoveItem(BaseModel):
        name: str
        angle: float
        duration: float = 0.3

    class MultiMoveBody(BaseModel):
        servos: List[MultiMoveItem]

    class SaveExpressionBody(BaseModel):
        angles: Dict[str, float]

    class LipSyncTestBody(BaseModel):
        text: str

    # --- Routes -----------------------------------------------------------

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/status")
    async def status():
        return {
            "connected": serial.is_connected,
            "port": serial.port,
            "idle_running": idle_running["value"],
        }

    @app.post("/api/connect")
    async def connect():
        try:
            port = serial.connect()
            _init_positions()
            expr = cfg.get_expression("neutral")
            if expr and serial.is_connected:
                cmds = []
                for servo_name, angle in expr.items():
                    pin = cfg.pin_for(servo_name)
                    if pin is not None:
                        cmds.append({"servo_id": pin, "angle": float(angle), "duration": 0.8})
                        positions[servo_name] = float(angle)
                if cmds:
                    serial.send_move_multiple(cmds)
            return {"connected": True, "port": port}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/disconnect")
    async def disconnect():
        idle_running["value"] = False
        serial.disconnect()
        _init_positions()
        return {"ok": True}

    @app.get("/api/servos")
    async def get_servos():
        return _servo_response()

    @app.post("/api/servo/{name}/move")
    async def move_servo(name: str, body: MoveBody):
        _reject_if_idle()
        pin = cfg.pin_for(name)
        if pin is None:
            raise HTTPException(status_code=404, detail=f"Unknown servo: {name}")
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        serial.send_move_servo(pin, body.angle, body.duration)
        positions[name] = body.angle
        return {"ok": True, "name": name, "angle": body.angle}

    @app.post("/api/servos/move-multiple")
    async def move_multiple(body: MultiMoveBody):
        _reject_if_idle()
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        # Build list (pin can be 0 for LeftJaw - must use "is None" check)
        items = []
        for item in body.servos:
            pin = cfg.pin_for(item.name)
            if pin is None:
                raise HTTPException(status_code=404, detail=f"Unknown servo: {item.name}")
            items.append((item.name, pin, item.angle, item.duration))
            positions[item.name] = item.angle
        cmds = [
            {"servo_id": pin, "angle": angle}
            for _name, pin, angle, _dur in items
        ]
        print(f"[set-angles] sending {len(cmds)} servos: "
              + ", ".join(f"pin={c['servo_id']} angle={c['angle']:.1f}" for c in cmds))
        serial.send_set_angles(cmds)
        return {"ok": True}

    @app.post("/api/calibrate")
    async def calibrate():
        """Move all servos to neutral position (same as applying the neutral expression)."""
        _reject_if_idle()
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        expr = cfg.get_expression("neutral")
        if not expr:
            raise HTTPException(status_code=500, detail="No neutral expression defined in config")
        cmds = []
        for servo_name, angle in expr.items():
            pin = cfg.pin_for(servo_name)
            if pin is None:
                continue
            cmds.append({"servo_id": pin, "angle": float(angle), "duration": 0.4})
            positions[servo_name] = float(angle)
        serial.send_move_multiple(cmds)
        return {"ok": True, "expression": "neutral"}

    @app.post("/api/stop")
    async def stop():
        try:
            if serial.is_connected:
                serial.send_stop()
        except Exception:
            pass
        serial.disconnect()
        _init_positions()
        return {"ok": True}

    # wink_right / wink_left are motion sequences — exclude from static expr list;
    # they are testable via the dedicated /api/eyes/wink-* endpoints instead.
    _EXPRESSION_HIDDEN = frozenset(("eyes_open", "eyes_closed", "wink_right", "wink_left"))

    @app.get("/api/expressions")
    async def get_expressions():
        """Return expressions for the UI list; eyes_open and eyes_closed are excluded (they are in Eyes section)."""
        filtered = {k: v for k, v in cfg.expressions.items() if k not in _EXPRESSION_HIDDEN}
        return {"expressions": filtered}

    @app.post("/api/expressions/{name}")
    async def save_expression(name: str, body: SaveExpressionBody):
        cfg.save_expression(name, body.angles)
        return {"ok": True, "name": name}

    @app.post("/api/expressions/{name}/apply")
    async def apply_expression(name: str):
        _reject_if_idle()
        expr = cfg.get_expression(name)
        if expr is None:
            raise HTTPException(status_code=404, detail=f"Unknown expression: {name}")
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        cmds = []
        for servo_name, angle in expr.items():
            pin = cfg.pin_for(servo_name)
            if pin is None:
                continue
            cmds.append({"servo_id": pin, "angle": angle, "duration": 0.4})
            positions[servo_name] = angle
        serial.send_move_multiple(cmds)
        return {"ok": True, "name": name, "positions": positions}

    @app.delete("/api/expressions/{name}")
    async def delete_expression(name: str):
        if not cfg.delete_expression(name):
            raise HTTPException(status_code=404, detail=f"Unknown expression: {name}")
        return {"ok": True}

    @app.get("/api/debug/esp-log")
    async def esp_log():
        """Return recent ESP32 serial output for debugging."""
        return {"lines": serial.get_log(last_n=100)}

    @app.post("/api/debug/esp-log/clear")
    async def clear_esp_log():
        serial.clear_log()
        return {"ok": True}

    @app.post("/api/config/reload")
    async def reload_config():
        cfg.reload()
        _init_positions()
        return _servo_response()

    # --- Lip Sync Test (TTS + jaw/lip movement without full brain) ----------

    @app.post("/api/lip-sync/test")
    def lip_sync_test(body: LipSyncTestBody):
        """Generate TTS with ElevenLabs (with timestamps), play audio, and drive jaw/lip servos in sync.
        Requires ELEVENLABS_API_KEY in environment. Idle must be Off. Connect to ESP32 first."""
        _reject_if_idle()
        if not serial.is_connected:
            raise HTTPException(
                status_code=503,
                detail="Not connected to ESP32. Connect first.",
            )
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="Text cannot be empty.")
        if not os.getenv("ELEVENLABS_API_KEY"):
            raise HTTPException(
                status_code=503,
                detail="ELEVENLABS_API_KEY not set. Add it to .env to test lip sync.",
            )

        from brain.speaking.main import Speaking
        from brain.movement.lip_sync import build_viseme_timeline

        lip_sync_config = cfg.get_lip_sync_config()
        try:
            speaking = Speaking()
            audio_bytes, alignment = speaking.generate_audio(body.text.strip())
        except Exception as e:
            err_str = str(e).lower()
            if "402" in err_str or "payment_required" in err_str or "paid_plan_required" in err_str:
                raise HTTPException(
                    status_code=402,
                    detail=(
                        "This voice requires a paid ElevenLabs plan. "
                        "Free tier: set voice_id in brain/config.py to a default voice, e.g. "
                        "Rachel: 21m00Tcm4TlvDq8ikWAM (see ElevenLabs dashboard → Default voices)."
                    ),
                )
            raise HTTPException(status_code=502, detail=f"TTS failed: {e}")

        if not audio_bytes:
            raise HTTPException(status_code=502, detail="TTS returned no audio.")

        if alignment is None:
            speaking.play_audio(audio_bytes)
            return {"ok": True, "lip_sync": False, "reason": "no alignment from TTS"}

        if not lip_sync_config.get("enabled", True):
            speaking.play_audio(audio_bytes)
            return {"ok": True, "lip_sync": False, "reason": "lip_sync disabled in config"}

        timeline = build_viseme_timeline(alignment, lip_sync_config)
        transition = float(lip_sync_config.get("transition_duration", 0.05))
        timeline_with_pins: List[tuple] = []
        for ts, angles_dict in timeline:
            cmds = []
            for name, angle in angles_dict.items():
                pin = cfg.pin_for(name)
                if pin is not None:
                    cmds.append({
                        "servo_id": pin,
                        "angle": round(float(angle), 1),
                        "duration": transition,
                    })
            if cmds:
                timeline_with_pins.append((ts, cmds))

        def run_timeline():
            origin = time.monotonic()
            for ts, cmds in timeline_with_pins:
                wait = ts - (time.monotonic() - origin)
                if wait > 0:
                    time.sleep(wait)
                if not serial.is_connected:
                    return
                try:
                    serial.send_move_multiple(cmds)
                except Exception as e:
                    print(f"[lip-sync-test] send error: {e}")

        thread = threading.Thread(target=run_timeline, daemon=True)
        thread.start()
        speaking.play_audio(audio_bytes)
        # Explicitly close mouth after playback
        jaw_closed = lip_sync_config.get("jaw_closed", {})
        close_cmds = []
        for name in ("LeftJaw", "RightJaw"):
            if name in jaw_closed:
                pin = cfg.pin_for(name)
                if pin is not None:
                    close_cmds.append({"servo_id": pin, "angle": float(jaw_closed[name]), "duration": 0.1})
                positions[name] = float(jaw_closed[name])
        if close_cmds and serial.is_connected:
            serial.send_move_multiple(close_cmds)
        return {"ok": True, "lip_sync": True}

    # --- Eyes (open / closed / blink) ----------------------------------------

    def _eyes_angles(expression_name: str) -> List[tuple]:
        """Return [(servo_name, pin, angle), ...] for the four eyelid servos from an expression."""
        expr = cfg.get_expression(expression_name)
        if not expr:
            return []
        out = []
        for name in ("EyeLidLeftDown", "EyeLidLeftUp", "EyeLidRightDown", "EyeLidRightUp"):
            if name in expr:
                pin = cfg.pin_for(name)
                if pin is not None:
                    out.append((name, pin, expr[name]))
        return out

    def _gaze_angles(expression_name: str) -> List[tuple]:
        """Return [(servo_name, pin, angle), ...] for EyeYAxis and EyeXAxis from an expression."""
        expr = cfg.get_expression(expression_name)
        if not expr:
            return []
        out = []
        for name in ("EyeYAxis", "EyeXAxis"):
            if name in expr:
                pin = cfg.pin_for(name)
                if pin is not None:
                    out.append((name, pin, expr[name]))
        return out

    @app.post("/api/eyes/center")
    async def eyes_center():
        """Move gaze to neutral center (EyeYAxis, EyeXAxis from neutral expression)."""
        _reject_if_idle()
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        items = _gaze_angles("neutral")
        if not items:
            raise HTTPException(status_code=500, detail="neutral expression has no EyeYAxis/EyeXAxis")
        cmds = [{"servo_id": pin, "angle": angle, "duration": 0.2} for _n, pin, angle in items]
        for _n, pin, angle in items:
            positions[_n] = angle
        serial.send_move_multiple(cmds)
        return {"ok": True, "expression": "neutral"}

    @app.post("/api/eyes/random-look")
    async def eyes_random_look():
        """One-shot random look: move gaze to random offset from center, hold, then return."""
        _reject_if_idle()
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        neutral = cfg.get_expression("neutral") or {}
        cx = neutral.get("EyeXAxis")
        cy = neutral.get("EyeYAxis")
        if cx is None or cy is None:
            raise HTTPException(status_code=500, detail="neutral expression needs EyeXAxis and EyeYAxis")
        cx, cy = float(cx), float(cy)
        idle = cfg._raw.get("idle", {})
        fraction = float(idle.get("gaze_extent_fraction", 0.75))
        move_dur = float(idle.get("gaze_move_duration", 0.25))
        return_dur = float(idle.get("gaze_return_duration", 0.3))
        hold_min = float(idle.get("gaze_hold_min", 1.5))
        hold_max = float(idle.get("gaze_hold_max", 3.5))
        lim_x = cfg.servos.get("EyeXAxis", {})
        lim_y = cfg.servos.get("EyeYAxis", {})
        mn_x = float(lim_x.get("min_angle", 0))
        mx_x = float(lim_x.get("max_angle", 180))
        mn_y = float(lim_y.get("min_angle", 0))
        mx_y = float(lim_y.get("max_angle", 180))
        ext_x = fraction * (mx_x - mn_x) / 2
        ext_y = fraction * (mx_y - mn_y) / 2
        x = max(mn_x, min(mx_x, cx + random.uniform(-ext_x, ext_x)))
        y = max(mn_y, min(mx_y, cy + random.uniform(-ext_y, ext_y)))
        pin_x = cfg.pin_for("EyeXAxis")
        pin_y = cfg.pin_for("EyeYAxis")
        if pin_x is None or pin_y is None:
            raise HTTPException(status_code=500, detail="EyeXAxis/EyeYAxis not in config")
        serial.send_move_multiple([
            {"servo_id": pin_x, "angle": x, "duration": move_dur},
            {"servo_id": pin_y, "angle": y, "duration": move_dur},
        ])
        positions["EyeXAxis"] = x
        positions["EyeYAxis"] = y
        hold = random.uniform(hold_min, hold_max)
        await asyncio.sleep(hold)
        serial.send_move_multiple([
            {"servo_id": pin_x, "angle": cx, "duration": return_dur},
            {"servo_id": pin_y, "angle": cy, "duration": return_dur},
        ])
        positions["EyeXAxis"] = cx
        positions["EyeYAxis"] = cy
        return {"ok": True, "hold_sec": round(hold, 2)}

    @app.post("/api/eyes/close")
    async def eyes_close():
        _reject_if_idle()
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        items = _eyes_angles("eyes_closed")
        if not items:
            raise HTTPException(status_code=500, detail="eyes_closed expression not found")
        cmds = [{"servo_id": pin, "angle": angle, "duration": 0.2} for _n, pin, angle in items]
        for _n, pin, angle in items:
            positions[_n] = angle
        serial.send_move_multiple(cmds)
        return {"ok": True, "expression": "eyes_closed"}

    @app.post("/api/eyes/open")
    async def eyes_open():
        _reject_if_idle()
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        items = _eyes_angles("eyes_open")
        if not items:
            raise HTTPException(status_code=500, detail="eyes_open expression not found")
        cmds = [{"servo_id": pin, "angle": angle, "duration": 0.2} for _n, pin, angle in items]
        for _n, pin, angle in items:
            positions[_n] = angle
        serial.send_move_multiple(cmds)
        return {"ok": True, "expression": "eyes_open"}

    _BLINK_CLOSE_DURATION = 0.06
    _BLINK_HOLD_DURATION = 0.12
    _BLINK_OPEN_DURATION = 0.08

    @app.post("/api/eyes/blink-left")
    async def eyes_blink_left():
        _reject_if_idle()
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        closed = [(n, p, a) for n, p, a in _eyes_angles("eyes_closed") if "Left" in n]
        open_ = [(n, p, a) for n, p, a in _eyes_angles("eyes_open") if "Left" in n]
        if not closed or not open_:
            raise HTTPException(status_code=500, detail="eyes_closed/eyes_open missing left eye")
        serial.send_move_multiple([{"servo_id": p, "angle": a, "duration": _BLINK_CLOSE_DURATION} for _n, p, a in closed])
        await asyncio.sleep(_BLINK_CLOSE_DURATION + _BLINK_HOLD_DURATION)
        serial.send_move_multiple([{"servo_id": p, "angle": a, "duration": _BLINK_OPEN_DURATION} for _n, p, a in open_])
        for _n, _p, a in open_:
            positions[_n] = a
        return {"ok": True}

    @app.post("/api/eyes/blink-right")
    async def eyes_blink_right():
        _reject_if_idle()
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        closed = [(n, p, a) for n, p, a in _eyes_angles("eyes_closed") if "Right" in n]
        open_ = [(n, p, a) for n, p, a in _eyes_angles("eyes_open") if "Right" in n]
        if not closed or not open_:
            raise HTTPException(status_code=500, detail="eyes_closed/eyes_open missing right eye")
        serial.send_move_multiple([{"servo_id": p, "angle": a, "duration": _BLINK_CLOSE_DURATION} for _n, p, a in closed])
        await asyncio.sleep(_BLINK_CLOSE_DURATION + _BLINK_HOLD_DURATION)
        serial.send_move_multiple([{"servo_id": p, "angle": a, "duration": _BLINK_OPEN_DURATION} for _n, p, a in open_])
        for _n, _p, a in open_:
            positions[_n] = a
        return {"ok": True}

    @app.post("/api/eyes/blink-both")
    async def eyes_blink_both():
        _reject_if_idle()
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        closed = _eyes_angles("eyes_closed")
        open_ = _eyes_angles("eyes_open")
        if not closed or not open_:
            raise HTTPException(status_code=500, detail="eyes_closed/eyes_open not found")
        serial.send_move_multiple([{"servo_id": p, "angle": a, "duration": _BLINK_CLOSE_DURATION} for _n, p, a in closed])
        await asyncio.sleep(_BLINK_CLOSE_DURATION + _BLINK_HOLD_DURATION)
        serial.send_move_multiple([{"servo_id": p, "angle": a, "duration": _BLINK_OPEN_DURATION} for _n, p, a in open_])
        for _n, _p, a in open_:
            positions[_n] = a
        return {"ok": True}

    # ── wink (one eye close → hold → open) ───────────────────────────────────

    _WINK_CLOSE_DURATION = 0.06
    _WINK_HOLD_DURATION = 0.14
    _WINK_OPEN_DURATION = 0.08

    @app.post("/api/eyes/wink-right")
    async def eyes_wink_right():
        """Close right eye, hold briefly, then reopen — tests the wink animation."""
        _reject_if_idle()
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        # Use wink_right expression if available, else filter right eye from eyes_closed
        closed = _eyes_angles("wink_right")
        if not closed:
            closed = [(n, p, a) for n, p, a in _eyes_angles("eyes_closed") if "Right" in n]
        open_ = [(n, p, a) for n, p, a in _eyes_angles("eyes_open") if "Right" in n]
        if not closed:
            raise HTTPException(status_code=500, detail="wink_right / eyes_closed right eye not configured")
        serial.send_move_multiple([{"servo_id": p, "angle": a, "duration": _WINK_CLOSE_DURATION} for _n, p, a in closed])
        await asyncio.sleep(_WINK_CLOSE_DURATION + _WINK_HOLD_DURATION)
        if open_:
            serial.send_set_angles([{"servo_id": p, "angle": a} for _n, p, a in open_])
            for _n, _p, a in open_:
                positions[_n] = a
        await asyncio.sleep(_WINK_OPEN_DURATION + 0.05)
        return {"ok": True}

    @app.post("/api/eyes/wink-left")
    async def eyes_wink_left():
        """Close left eye, hold briefly, then reopen — tests the wink animation."""
        _reject_if_idle()
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        closed = _eyes_angles("wink_left")
        if not closed:
            closed = [(n, p, a) for n, p, a in _eyes_angles("eyes_closed") if "Left" in n]
        open_ = [(n, p, a) for n, p, a in _eyes_angles("eyes_open") if "Left" in n]
        if not closed:
            raise HTTPException(status_code=500, detail="wink_left / eyes_closed left eye not configured")
        serial.send_move_multiple([{"servo_id": p, "angle": a, "duration": _WINK_CLOSE_DURATION} for _n, p, a in closed])
        await asyncio.sleep(_WINK_CLOSE_DURATION + _WINK_HOLD_DURATION)
        if open_:
            serial.send_set_angles([{"servo_id": p, "angle": a} for _n, p, a in open_])
            for _n, _p, a in open_:
                positions[_n] = a
        await asyncio.sleep(_WINK_OPEN_DURATION + 0.05)
        return {"ok": True}

    # ── neck quick controls (manual mode) ────────────────────────────────────

    def _neck_neutral():
        """Return (yaw_neutral, pitch_neutral, yaw_pin, pitch_pin) from config."""
        neutral = cfg.get_expression("neutral") or {}
        cy = float(neutral.get("NeckYaw", 180))
        cp = float(neutral.get("NeckPitch", 200))
        p_yaw = cfg.pin_for("NeckYaw")
        p_pitch = cfg.pin_for("NeckPitch")
        return cy, cp, p_yaw, p_pitch

    def _neck_limits(servo_name: str):
        s = cfg.servos.get(servo_name, {})
        mn = float(s.get("min_angle", 0))
        mx = float(s.get("max_angle", 360))
        return min(mn, mx), max(mn, mx)

    @app.post("/api/neck/look-left")
    async def neck_look_left():
        """Turn head left (40 % of yaw range from neutral)."""
        _reject_if_idle()
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        cy, _cp, p_yaw, _p_pitch = _neck_neutral()
        if p_yaw is None:
            raise HTTPException(status_code=404, detail="NeckYaw not configured")
        mn, _mx = _neck_limits("NeckYaw")
        target = max(mn, cy - (cy - mn) * 0.4)
        serial.send_move_multiple([{"servo_id": p_yaw, "angle": target, "duration": 0.5}])
        positions["NeckYaw"] = target
        return {"ok": True, "angle": round(target, 1)}

    @app.post("/api/neck/look-right")
    async def neck_look_right():
        """Turn head right (40 % of yaw range from neutral)."""
        _reject_if_idle()
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        cy, _cp, p_yaw, _p_pitch = _neck_neutral()
        if p_yaw is None:
            raise HTTPException(status_code=404, detail="NeckYaw not configured")
        _mn, mx = _neck_limits("NeckYaw")
        target = min(mx, cy + (mx - cy) * 0.4)
        serial.send_move_multiple([{"servo_id": p_yaw, "angle": target, "duration": 0.5}])
        positions["NeckYaw"] = target
        return {"ok": True, "angle": round(target, 1)}

    @app.post("/api/neck/look-up")
    async def neck_look_up():
        """Tilt head up (40 % of pitch range from neutral)."""
        _reject_if_idle()
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        _cy, cp, _p_yaw, p_pitch = _neck_neutral()
        if p_pitch is None:
            raise HTTPException(status_code=404, detail="NeckPitch not configured")
        mn, mx = _neck_limits("NeckPitch")
        target = max(mn, min(mx, cp + (mx - cp) * 0.4))
        serial.send_move_multiple([{"servo_id": p_pitch, "angle": target, "duration": 0.5}])
        positions["NeckPitch"] = target
        return {"ok": True, "angle": round(target, 1)}

    @app.post("/api/neck/look-down")
    async def neck_look_down():
        """Tilt head down (40 % of pitch range from neutral)."""
        _reject_if_idle()
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        _cy, cp, _p_yaw, p_pitch = _neck_neutral()
        if p_pitch is None:
            raise HTTPException(status_code=404, detail="NeckPitch not configured")
        mn, mx = _neck_limits("NeckPitch")
        target = max(mn, min(mx, cp - (cp - mn) * 0.4))
        serial.send_move_multiple([{"servo_id": p_pitch, "angle": target, "duration": 0.5}])
        positions["NeckPitch"] = target
        return {"ok": True, "angle": round(target, 1)}

    @app.post("/api/neck/center")
    async def neck_center():
        """Return head to neutral position."""
        _reject_if_idle()
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        cy, cp, p_yaw, p_pitch = _neck_neutral()
        cmds = []
        if p_yaw is not None:
            cmds.append({"servo_id": p_yaw, "angle": cy, "duration": 0.5})
            positions["NeckYaw"] = cy
        if p_pitch is not None:
            cmds.append({"servo_id": p_pitch, "angle": cp, "duration": 0.5})
            positions["NeckPitch"] = cp
        if cmds:
            serial.send_move_multiple(cmds)
        return {"ok": True}

    @app.post("/api/neck/nod")
    async def neck_nod():
        """Execute a brief nod (pitch down → return to neutral)."""
        _reject_if_idle()
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        _cy, cp, _p_yaw, p_pitch = _neck_neutral()
        if p_pitch is None:
            raise HTTPException(status_code=404, detail="NeckPitch not configured")
        mn, mx = _neck_limits("NeckPitch")
        nod_angle = max(mn, min(mx, cp - 12.0))
        serial.send_move_multiple([{"servo_id": p_pitch, "angle": nod_angle, "duration": 0.20}])
        await asyncio.sleep(0.30)
        serial.send_move_multiple([{"servo_id": p_pitch, "angle": cp, "duration": 0.25}])
        await asyncio.sleep(0.30)
        positions["NeckPitch"] = cp
        return {"ok": True}

    @app.post("/api/neck/shake")
    async def neck_shake():
        """Execute a head shake (yaw left → right → centre)."""
        _reject_if_idle()
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        cy, _cp, p_yaw, _p_pitch = _neck_neutral()
        if p_yaw is None:
            raise HTTPException(status_code=404, detail="NeckYaw not configured")
        mn, mx = _neck_limits("NeckYaw")
        left = max(mn, cy - 15.0)
        right = min(mx, cy + 15.0)
        serial.send_move_multiple([{"servo_id": p_yaw, "angle": left, "duration": 0.20}])
        await asyncio.sleep(0.30)
        serial.send_move_multiple([{"servo_id": p_yaw, "angle": right, "duration": 0.25}])
        await asyncio.sleep(0.40)
        serial.send_move_multiple([{"servo_id": p_yaw, "angle": cy, "duration": 0.20}])
        await asyncio.sleep(0.25)
        positions["NeckYaw"] = cy
        return {"ok": True}

    # ── person tracking toggle ────────────────────────────────────────────────

    @app.get("/api/person-tracking")
    async def get_person_tracking():
        try:
            if PERSON_TRACKING_PATH.exists():
                data = json.loads(PERSON_TRACKING_PATH.read_text())
                return {"person_tracking_enabled": bool(data.get("person_tracking_enabled", False))}
        except Exception:
            pass
        return {"person_tracking_enabled": False}

    @app.post("/api/person-tracking")
    async def set_person_tracking(body: dict):
        enabled = bool(body.get("person_tracking_enabled", False))
        PERSON_TRACKING_PATH.parent.mkdir(parents=True, exist_ok=True)
        PERSON_TRACKING_PATH.write_text(json.dumps({"person_tracking_enabled": enabled}, indent=2))
        return {"person_tracking_enabled": enabled}

    # --- Local idle (manual mode: blink + random gaze; blocks servo/expression control) ----

    async def _do_blink():
        """One blink: close (timed move), hold, then open (instant set) so open is never dropped."""
        closed = _eyes_angles("eyes_closed")
        open_ = _eyes_angles("eyes_open")
        if not closed or not open_:
            return
        idle_cfg = cfg._raw.get("idle", {})
        close_dur = random.triangular(
            float(idle_cfg.get("blink_close_min", 0.04)),
            float(idle_cfg.get("blink_close_max", 0.08)),
        )
        hold_dur = random.triangular(
            float(idle_cfg.get("blink_hold_min", 0.06)),
            float(idle_cfg.get("blink_hold_max", 0.15)),
        )
        open_dur = random.triangular(
            float(idle_cfg.get("blink_open_min", 0.04)),
            float(idle_cfg.get("blink_open_max", 0.10)),
        )
        serial.send_move_multiple([{"servo_id": p, "angle": a, "duration": close_dur} for _n, p, a in closed])
        await asyncio.sleep(close_dur + hold_dur)
        await asyncio.sleep(0.02)
        serial.send_set_angles([{"servo_id": p, "angle": a} for _n, p, a in open_])
        for _n, _p, a in open_:
            positions[_n] = a
        await asyncio.sleep(open_dur)

    async def _do_random_look():
        """One random look from center, hold, then return. Same logic as eyes_random_look."""
        neutral = cfg.get_expression("neutral") or {}
        cx = neutral.get("EyeXAxis")
        cy = neutral.get("EyeYAxis")
        if cx is None or cy is None:
            return
        cx, cy = float(cx), float(cy)
        idle_cfg = cfg._raw.get("idle", {})
        fraction = float(idle_cfg.get("gaze_extent_fraction", 0.75))
        move_dur = float(idle_cfg.get("gaze_move_duration", 0.25))
        return_dur = float(idle_cfg.get("gaze_return_duration", 0.3))
        hold_min = float(idle_cfg.get("gaze_hold_min", 1.5))
        hold_max = float(idle_cfg.get("gaze_hold_max", 3.5))
        lim_x = cfg.servos.get("EyeXAxis", {})
        lim_y = cfg.servos.get("EyeYAxis", {})
        mn_x = float(lim_x.get("min_angle", 0))
        mx_x = float(lim_x.get("max_angle", 180))
        mn_y = float(lim_y.get("min_angle", 0))
        mx_y = float(lim_y.get("max_angle", 180))
        ext_x = fraction * (mx_x - mn_x) / 2
        ext_y = fraction * (mx_y - mn_y) / 2
        x = max(mn_x, min(mx_x, cx + random.uniform(-ext_x, ext_x)))
        y = max(mn_y, min(mx_y, cy + random.uniform(-ext_y, ext_y)))
        pin_x = cfg.pin_for("EyeXAxis")
        pin_y = cfg.pin_for("EyeYAxis")
        if pin_x is None or pin_y is None:
            return
        serial.send_move_multiple([
            {"servo_id": pin_x, "angle": x, "duration": move_dur},
            {"servo_id": pin_y, "angle": y, "duration": move_dur},
        ])
        positions["EyeXAxis"] = x
        positions["EyeYAxis"] = y
        hold = random.uniform(hold_min, hold_max)
        await asyncio.sleep(hold)
        serial.send_move_multiple([
            {"servo_id": pin_x, "angle": cx, "duration": return_dur},
            {"servo_id": pin_y, "angle": cy, "duration": return_dur},
        ])
        positions["EyeXAxis"] = cx
        positions["EyeYAxis"] = cy
        await asyncio.sleep(return_dur)

    async def _idle_loop():
        """Run blink and random gaze at random intervals while idle_running and connected."""
        idle_cfg = cfg._raw.get("idle", {})
        interval_min = float(idle_cfg.get("interval_min", 2.0))
        interval_max = float(idle_cfg.get("interval_max", 6.0))
        blink_chance = float(idle_cfg.get("blink_chance", 0.4))
        neutral = cfg.get_expression("neutral") or {}
        has_gaze = "EyeXAxis" in neutral and "EyeYAxis" in neutral
        while idle_running["value"]:
            await asyncio.sleep(random.uniform(interval_min, interval_max))
            if not idle_running["value"]:
                break
            if not serial.is_connected:
                continue
            do_gaze = has_gaze and random.random() > blink_chance
            try:
                if do_gaze:
                    await _do_random_look()
                else:
                    await _do_blink()
            except Exception as e:
                print(f"[idle_loop] error: {e}")

    @app.get("/api/idle-running")
    async def get_idle_running():
        return {"idle_running": idle_running["value"]}

    @app.post("/api/idle-running")
    async def set_idle_running(body: dict):
        want = bool(body.get("idle_running", False))
        if want and not serial.is_connected:
            raise HTTPException(status_code=503, detail="Connect to ESP32 first to run Idle.")
        idle_running["value"] = want
        if want:
            asyncio.create_task(_idle_loop())
        return {"idle_running": idle_running["value"]}

    # --- Idle (brain) file: brain reads this when it runs; optional for manual mode ----

    @app.get("/api/idle-enabled")
    async def get_idle_enabled():
        try:
            if IDLE_ENABLED_PATH.exists():
                data = json.loads(IDLE_ENABLED_PATH.read_text())
                return {"idle_enabled": bool(data.get("idle_enabled", True))}
        except Exception:
            pass
        return {"idle_enabled": True}

    @app.post("/api/idle-enabled")
    async def set_idle_enabled(body: dict):
        enabled = body.get("idle_enabled", True)
        IDLE_ENABLED_PATH.parent.mkdir(parents=True, exist_ok=True)
        IDLE_ENABLED_PATH.write_text(json.dumps({"idle_enabled": bool(enabled)}, indent=2))
        return {"idle_enabled": bool(enabled)}

    # --- Mode switching -------------------------------------------------------

    @app.get("/api/mode")
    async def get_mode():
        return {"mode": current_mode["value"]}

    @app.post("/api/mode")
    async def set_mode(body: dict):
        mode = body.get("mode", "manual")
        if mode not in ("manual", "auto"):
            raise HTTPException(status_code=400, detail="mode must be 'manual' or 'auto'")
        old = current_mode["value"]
        if mode == old:
            return {"mode": mode}

        if mode == "auto":
            idle_running["value"] = False
            if not serial.is_connected:
                raise HTTPException(status_code=503, detail="Connect to ESP32 first")
            current_mode["value"] = "auto"
        else:
            if brain_runner.running:
                await brain_runner.stop()
            current_mode["value"] = "manual"
        return {"mode": current_mode["value"]}

    # --- Brain control (auto mode) -------------------------------------------

    @app.post("/api/brain/start")
    async def brain_start():
        if current_mode["value"] != "auto":
            raise HTTPException(status_code=409, detail="Switch to auto mode first")
        if brain_runner.running:
            raise HTTPException(status_code=409, detail="Brain is already running")
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        await brain_runner.start(serial, event_bus)
        return {"ok": True}

    @app.post("/api/brain/stop")
    async def brain_stop():
        await brain_runner.stop()
        return {"ok": True}

    @app.get("/api/brain/status")
    async def brain_status():
        from brain.state import robot_state
        return {
            "running": brain_runner.running,
            "activity": robot_state.get_activity(),
            "expression": robot_state.get_current_expression(),
        }

    # --- Data browser API (conversations, surroundings, logs) ---------------

    DATA_DIR = PROJECT_ROOT / "brain" / "data"
    CONVERSATIONS_DIR = DATA_DIR / "conversations"
    SURROUNDINGS_DIR = DATA_DIR / "surroundings"
    LOGS_DIR = DATA_DIR / "logs"

    @app.get("/api/data/conversations")
    async def list_conversations():
        if not CONVERSATIONS_DIR.exists():
            return {"conversations": []}
        items = []
        for p in sorted(CONVERSATIONS_DIR.iterdir(), reverse=True):
            if not p.is_dir():
                continue
            meta = {"id": p.name, "start_time": None, "message_count": 0, "duration_s": None}
            conv_json = p / "conversation.json"
            if conv_json.exists():
                try:
                    data = json.loads(conv_json.read_text())
                    meta["start_time"] = data.get("conversation_start_time")
                    meta["message_count"] = len(data.get("messages", []))
                    if data.get("conversation_start_time") and data.get("conversation_end_time"):
                        meta["duration_s"] = round(data["conversation_end_time"] - data["conversation_start_time"], 1)
                except Exception:
                    pass
            audio_files = [f.name for f in p.iterdir() if f.suffix in (".wav", ".mp3")]
            meta["audio_files"] = audio_files
            items.append(meta)
        return {"conversations": items}

    def _find_nearby_surroundings(start_time, end_time, margin=120):
        """Find surroundings images taken within `margin` seconds of the conversation window."""
        from datetime import datetime
        images_dir = SURROUNDINGS_DIR / "images"
        contexts_dir = SURROUNDINGS_DIR / "contexts"
        if not images_dir.exists():
            return []
        t_start = (start_time or 0) - margin
        t_end = (end_time or start_time or 0) + margin
        matches = []
        for img in images_dir.iterdir():
            if img.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            try:
                dt = datetime.strptime(img.stem, "%Y-%m-%d_%H-%M-%S")
                ts = dt.timestamp()
            except Exception:
                continue
            if t_start <= ts <= t_end:
                has_context = (contexts_dir / f"{img.stem}.txt").exists() if contexts_dir.exists() else False
                matches.append({
                    "timestamp": img.stem,
                    "image": img.name,
                    "has_context": has_context,
                })
        matches.sort(key=lambda x: x["timestamp"])
        return matches

    @app.get("/api/data/conversations/{conv_id}")
    async def get_conversation(conv_id: str):
        conv_dir = CONVERSATIONS_DIR / conv_id
        if not conv_dir.exists():
            raise HTTPException(status_code=404, detail="Conversation not found")
        conv_json = conv_dir / "conversation.json"
        if not conv_json.exists():
            raise HTTPException(status_code=404, detail="conversation.json not found")
        try:
            data = json.loads(conv_json.read_text())
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error reading conversation: {e}")
        audio_files = [f.name for f in conv_dir.iterdir() if f.suffix in (".wav", ".mp3")]
        data["audio_files"] = audio_files
        data["surroundings"] = _find_nearby_surroundings(
            data.get("conversation_start_time"),
            data.get("conversation_end_time"),
        )
        return data

    @app.get("/api/data/conversations/{conv_id}/audio/{filename}")
    async def get_conversation_audio(conv_id: str, filename: str):
        file_path = CONVERSATIONS_DIR / conv_id / filename
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="Audio file not found")
        suffix = file_path.suffix.lower()
        media = "audio/mpeg" if suffix == ".mp3" else "audio/wav"
        return Response(content=file_path.read_bytes(), media_type=media,
                        headers={"Content-Disposition": f"inline; filename=\"{filename}\""})

    @app.get("/api/data/surroundings")
    async def list_surroundings():
        images_dir = SURROUNDINGS_DIR / "images"
        contexts_dir = SURROUNDINGS_DIR / "contexts"
        items = []
        if images_dir.exists():
            for img in sorted(images_dir.iterdir(), reverse=True):
                if img.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                    continue
                stem = img.stem
                has_context = (contexts_dir / f"{stem}.txt").exists() if contexts_dir.exists() else False
                items.append({
                    "timestamp": stem,
                    "image": img.name,
                    "has_context": has_context,
                })
        return {"surroundings": items}

    @app.get("/api/data/surroundings/images/{filename}")
    async def get_surroundings_image(filename: str):
        file_path = SURROUNDINGS_DIR / "images" / filename
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Image not found")
        suffix = file_path.suffix.lower()
        media = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(suffix.lstrip("."), "image/jpeg")
        return Response(content=file_path.read_bytes(), media_type=media)

    @app.get("/api/data/surroundings/contexts/{filename}")
    async def get_surroundings_context(filename: str):
        file_path = SURROUNDINGS_DIR / "contexts" / filename
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Context file not found")
        return Response(content=file_path.read_text(encoding="utf-8"), media_type="text/plain")

    @app.get("/api/data/logs")
    async def list_logs():
        if not LOGS_DIR.exists():
            return {"logs": []}
        items = []
        for f in sorted(LOGS_DIR.iterdir(), reverse=True):
            if f.suffix == ".jsonl":
                items.append({
                    "filename": f.name,
                    "size_bytes": f.stat().st_size,
                    "modified": f.stat().st_mtime,
                })
        return {"logs": items}

    @app.get("/api/data/logs/{filename}")
    async def get_log_file(filename: str):
        file_path = LOGS_DIR / filename
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Log file not found")
        lines = []
        for line in file_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    lines.append(json.loads(line))
                except Exception:
                    pass
        return {"filename": filename, "events": lines}

    # --- WebSocket (real-time event stream) ----------------------------------

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def on_event(event):
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event.to_dict())
            except Exception:
                pass

        event_bus.subscribe(on_event)
        ws_clients.append(ws)
        try:
            history = event_bus.get_history(last_n=50)
            if history:
                await ws.send_json({"type": "history", "events": history})

            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30)
                    await ws.send_json(msg)
                except asyncio.TimeoutError:
                    await ws.send_json({"type": "ping"})
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            event_bus.unsubscribe(on_event)
            if ws in ws_clients:
                ws_clients.remove(ws)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app
