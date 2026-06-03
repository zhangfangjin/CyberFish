from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import uuid


DIRECTIONS = ("left", "right", "up", "down")

# 四个方向的互逆方向：left<->right、up<->down。
INVERSE_DIRECTIONS = {
    "left": "right",
    "right": "left",
    "up": "down",
    "down": "up",
}


def _new_node_id() -> str:
    return f"node-{uuid.uuid4().hex[:8]}"


def _default_topology() -> dict[str, str | None]:
    return {direction: None for direction in DIRECTIONS}


def sanitize_topology(raw: object) -> dict[str, str | None]:
    """把任意输入规整为恰好包含四个方向键、取值为非空字符串或 None 的拓扑。

    用于满足 Requirement 7.2/7.6：topology 字段必须恰好包含 left/right/up/down
    四个键，且取值为非空 node_id 字符串或 null；结构非法时回退为四个 null。
    """
    topology = _default_topology()
    if isinstance(raw, dict):
        for direction in DIRECTIONS:
            value = raw.get(direction)
            if isinstance(value, str) and value:
                topology[direction] = value
            else:
                topology[direction] = None
    return topology


@dataclass
class AppConfig:
    """运行配置，既服务单机演示，也保存多机拓扑校准结果。"""

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
    auto_topology: bool = True
    topology: dict[str, str | None] = field(default_factory=_default_topology)

    def normalized(self) -> "AppConfig":
        self.udp_port = int(self.udp_port)
        self.display_index = max(0, int(self.display_index))
        self.window_width = max(640, int(self.window_width))
        self.window_height = max(360, int(self.window_height))
        self.fish_count = min(200, max(1, int(self.fish_count)))
        self.speed_multiplier = min(4.0, max(0.1, float(self.speed_multiplier)))
        # auto_topology 必须为合法布尔值，否则回退为 True（Requirement 1.2）。
        if not isinstance(self.auto_topology, bool):
            self.auto_topology = True
        # 原地更新 topology，保持外部（如 TopologyCoordinator）持有的引用有效。
        sanitized = sanitize_topology(self.topology)
        if isinstance(self.topology, dict):
            self.topology.clear()
            self.topology.update(sanitized)
        else:
            self.topology = sanitized
        return self

    def to_dict(self) -> dict:
        return asdict(self)


def _merge_defaults(raw: dict) -> dict:
    defaults = AppConfig().to_dict()
    merged = defaults | raw
    merged["topology"] = sanitize_topology(raw.get("topology"))
    return merged


def topology_equal(
    left: dict[str, str | None],
    right: dict[str, str | None],
) -> bool:
    """逐方向比较两个拓扑是否等价，不依赖键顺序或文本格式（Requirement 7.3）。"""
    return all(left.get(direction) == right.get(direction) for direction in DIRECTIONS)


def load_config(path: Path) -> AppConfig:
    """读取配置；首次运行自动创建，旧配置缺字段时补默认值。"""
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


def save_config(path: Path, config: AppConfig) -> bool:
    """原子化写入配置文件。

    写入失败时保护原文件不被破坏，并返回 False（Requirement 7.7）。
    """
    payload = json.dumps(
        config.normalized().to_dict(),
        indent=2,
        ensure_ascii=False,
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")
        os.replace(temp_path, path)
        return True
    except OSError:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except (OSError, NameError, UnboundLocalError):
            pass
        return False


def assign_peer_to_single_direction(
    topology: dict[str, str | None],
    peer_id: str,
    direction: str,
) -> dict[str, str | None]:
    """把一个 peer 绑定到唯一方向，避免同一主机同时占用多条边。"""
    if direction not in DIRECTIONS:
        raise ValueError(f"Invalid topology direction: {direction}")
    for existing_direction in DIRECTIONS:
        if topology.get(existing_direction) == peer_id:
            topology[existing_direction] = None
    topology[direction] = peer_id
    return topology
