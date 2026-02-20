from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .serial_client import SerialClient
from .config_manager import ConfigManager

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_CONFIG = Path(__file__).parent.parent / "esp32" / "servo_data.json"


def create_app(
    config_path: Optional[str] = None,
    serial_port: Optional[str] = None,
) -> FastAPI:
    app = FastAPI(title="Manual Debug - Humanoid Robot")

    cfg = ConfigManager(config_path or DEFAULT_CONFIG)
    serial = SerialClient(port=serial_port)

    positions: Dict[str, float] = {}

    def _init_positions():
        positions.clear()
        for s in cfg.get_servo_list():
            positions[s["name"]] = cfg.calibrate_angle

    _init_positions()

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
        serial.disconnect()
        _init_positions()
        return {"ok": True}

    @app.get("/api/servos")
    async def get_servos():
        grouped = cfg.get_grouped_servo_list()
        for group_servos in grouped.values():
            for s in group_servos:
                s["current_angle"] = positions.get(s["name"], cfg.calibrate_angle)
        return {"groups": grouped, "calibrate_angle": cfg.calibrate_angle}

    @app.post("/api/servo/{name}/move")
    async def move_servo(name: str, body: MoveBody):
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
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        cmds = []
        for item in body.servos:
            pin = cfg.pin_for(item.name)
            if pin is None:
                raise HTTPException(status_code=404, detail=f"Unknown servo: {item.name}")
            cmds.append({"servo_id": pin, "angle": item.angle, "duration": item.duration})
            positions[item.name] = item.angle
        serial.send_move_multiple(cmds)
        return {"ok": True}

    @app.post("/api/calibrate")
    async def calibrate():
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        serial.send_calibrate()
        _init_positions()
        return {"ok": True, "angle": cfg.calibrate_angle}

    @app.post("/api/stop")
    async def stop():
        if not serial.is_connected:
            raise HTTPException(status_code=503, detail="Not connected to ESP32")
        serial.send_stop()
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

    @app.post("/api/config/reload")
    async def reload_config():
        cfg.reload()
        _init_positions()
        grouped = cfg.get_grouped_servo_list()
        for group_servos in grouped.values():
            for s in group_servos:
                s["current_angle"] = positions.get(s["name"], cfg.calibrate_angle)
        return {"groups": grouped, "calibrate_angle": cfg.calibrate_angle}

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app
