from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import uuid


DIRECTIONS = ("left", "right", "up", "down")


def _new_node_id() -> str:
    return f"node-{uuid.uuid4().hex[:8]}"


def _default_topology() -> dict[str, str | None]:
    return {direction: None for direction in DIRECTIONS}


@dataclass
class AppConfig:
    node_id: str = field(default_factory=_new_node_id)
    udp_port: int = 37777
    broadcast_host: str = "255.255.255.255"
    fullscreen: bool = False
    display_index: int = 0
    window_width: int = 1280
    window_height: int = 720
    fish_count: int = 12
    speed_multiplier: float = 1.0
    sound_enabled: bool = True
    network_enabled: bool = True
    topology: dict[str, str | None] = field(default_factory=_default_topology)

    def normalized(self) -> "AppConfig":
        self.udp_port = int(self.udp_port)
        self.display_index = max(0, int(self.display_index))
        self.window_width = max(640, int(self.window_width))
        self.window_height = max(360, int(self.window_height))
        self.fish_count = min(200, max(1, int(self.fish_count)))
        self.speed_multiplier = min(4.0, max(0.1, float(self.speed_multiplier)))
        merged_topology = _default_topology()
        merged_topology.update(
            {
                direction: value
                for direction, value in self.topology.items()
                if direction in merged_topology and (value is None or isinstance(value, str))
            }
        )
        self.topology = merged_topology
        return self

    def to_dict(self) -> dict:
        return asdict(self)


def _merge_defaults(raw: dict) -> dict:
    defaults = AppConfig().to_dict()
    merged = defaults | raw
    topology = defaults["topology"] | raw.get("topology", {})
    merged["topology"] = topology
    return merged


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        config = AppConfig().normalized()
        save_config(path, config)
        return config

    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError(f"Config file must contain a JSON object: {path}")

    config = AppConfig(**_merge_defaults(raw)).normalized()
    if config.to_dict() != raw:
        save_config(path, config)
    return config


def save_config(path: Path, config: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(config.normalized().to_dict(), handle, indent=2, ensure_ascii=False)
        handle.write("\n")
