import json
from pathlib import Path
from typing import Dict, Optional, Any, List


SERVO_GROUPS = {
    "Eyes": ["EyeLidLeftDown", "EyeLidLeftUp", "EyeLidRightDown", "EyeLidRightUp", "EyeYAxis", "EyeXAxis"],
    "Eyebrows": ["EyebrowInnerRight", "EyebrowInnerLeft", "EyebrowOuterRight", "EyebrowOuterLeft"],
    "Cheeks": ["RightCheekUp", "RightCheekDown", "LeftCheekUp", "LeftCheekDown"],
    "Mouth": ["LeftJaw", "RightJaw", "UpperLip"],
    "Neck": ["NeckYaw", "NeckPitch"],
}

# Single slider controls multiple servos in sync.
# direction: +1 means angle = center + offset, -1 means angle = center - offset
LINKED_CONTROLS = {
    "Mouth": {
        "label": "Jaw Open/Close",
        "slider_min": 25,
        "slider_max": 95,
        "slider_default": 25,
        "servos": {
            "RightJaw": {"center": 95, "direction": -1},
            "LeftJaw": {"center": 85, "direction": 1},
        },
    },
    "NeckYaw": {
        "label": "Neck Turn (Left/Right)",
        "slider_min": 0,
        "slider_max": 360,
        "slider_default": 180,
        "servos": {
            "NeckYaw": {"center": 180, "direction": 1},
        },
    },
    "NeckPitch": {
        "label": "Neck Tilt (Up/Down)",
        "slider_min": 50,
        "slider_max": 130,
        "slider_default": 90,
        "servos": {
            "NeckPitch": {"center": 90, "direction": 1},
        },
    },
}


class ConfigManager:
    """Loads, queries, and writes servo_data.json."""

    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        self._raw: Dict[str, Any] = {}
        self.servos: Dict[str, Dict[str, Any]] = {}
        self.global_config: Dict[str, Any] = {}
        self.expressions: Dict[str, Dict[str, float]] = {}
        self.name_to_pin: Dict[str, int] = {}
        self.reload()

    def reload(self):
        with open(self.config_path, "r") as f:
            self._raw = json.load(f)

        self.global_config = self._raw.get("global", {})
        self.servos = self._raw.get("servos", {})
        self.expressions = self._raw.get("expressions", {})

        self.name_to_pin = {}
        for name, cfg in self.servos.items():
            pin = cfg.get("pin")
            if pin is not None:
                self.name_to_pin[name] = int(pin)

    @property
    def calibrate_angle(self) -> float:
        return self.global_config.get("calibrate_angle", 90)

    def get_servo_list(self) -> List[Dict[str, Any]]:
        """Return a list of servo descriptors suitable for the UI."""
        result = []
        for name, cfg in self.servos.items():
            pin = cfg.get("pin")
            if pin is None:
                continue
            min_a = cfg.get("min_angle", 0)
            max_a = cfg.get("max_angle", 0)
            if min_a == 0 and max_a == 0:
                min_a, max_a = 0, 180
            result.append({
                "name": name,
                "pin": int(pin),
                "min_angle": min_a,
                "max_angle": max_a,
                "inverted": cfg.get("inverted", False),
            })
        return result

    def get_grouped_servo_list(self) -> Dict[str, List[Dict[str, Any]]]:
        all_servos = {s["name"]: s for s in self.get_servo_list()}
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        assigned = set()
        for group_name, names in SERVO_GROUPS.items():
            group = []
            for n in names:
                if n in all_servos:
                    group.append(all_servos[n])
                    assigned.add(n)
            if group:
                grouped[group_name] = group
        remaining = [s for n, s in all_servos.items() if n not in assigned]
        if remaining:
            grouped["Other"] = remaining
        return grouped

    def pin_for(self, name: str) -> Optional[int]:
        return self.name_to_pin.get(name)

    def get_linked_controls(self) -> Dict[str, Any]:
        """Return LINKED_CONTROLS filtered to servos that exist in the config."""
        result = {}
        for group, lc in LINKED_CONTROLS.items():
            filtered_servos = {
                name: mapping
                for name, mapping in lc["servos"].items()
                if name in self.servos
            }
            if filtered_servos:
                result[group] = {**lc, "servos": filtered_servos}
        return result

    def save_expression(self, name: str, angles: Dict[str, float]):
        self.expressions[name] = angles
        self._raw["expressions"] = self.expressions
        self._flush()

    def delete_expression(self, name: str) -> bool:
        if name in self.expressions:
            del self.expressions[name]
            self._raw["expressions"] = self.expressions
            self._flush()
            return True
        return False

    def get_expression(self, name: str) -> Optional[Dict[str, float]]:
        return self.expressions.get(name)

    def get_lip_sync_config(self) -> Dict[str, Any]:
        """Return the lip_sync section from servo_data.json for TTS lip-sync test."""
        return self._raw.get("lip_sync", {})

    def _flush(self):
        with open(self.config_path, "w") as f:
            json.dump(self._raw, f, indent=2)
            f.write("\n")
