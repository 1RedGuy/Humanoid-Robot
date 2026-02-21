import asyncio
import json
import random
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .serial_client import SerialClient
from .config_manager import ConfigManager

STATIC_DIR = Path(__file__).parent / "static"
PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "esp32" / "servo_data.json"
IDLE_ENABLED_PATH = PROJECT_ROOT / "brain" / "data" / "idle_enabled.json"


def create_app(
    config_path: Optional[str] = None,
    serial_port: Optional[str] = None,
) -> FastAPI:
    app = FastAPI(title="Manual Debug - Humanoid Robot")

    cfg = ConfigManager(config_path or DEFAULT_CONFIG)
    serial = SerialClient(port=serial_port)

    positions: Dict[str, float] = {}
    idle_running = {"value": False}  # mutable so closure can set it

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
        _reject_if_idle()
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        _init_positions()
        serial.send_calibrate()
        return {"ok": True, "angle": cfg.calibrate_angle}

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

    @app.get("/api/expressions")
    async def get_expressions():
        return {"expressions": cfg.expressions}

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
        """One-shot random look: move gaze to random offset from center, hold, then return. Works from manual_debug without the brain."""
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

    # --- Local idle (manual_debug: blink + random gaze; blocks servo/expression control) ----

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

    # --- Idle (brain) file: brain reads this when it runs; optional for manual_debug ----

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

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app
