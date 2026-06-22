from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import uuid


DIRECTIONS = ("left", "right", "up", "down")
ROLE_ADMIN = "admin"
ROLE_DISPLAY_NODE = "display_node"
ROLES = (ROLE_ADMIN, ROLE_DISPLAY_NODE)

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


def sanitize_role(raw: object) -> str:
    if isinstance(raw, str) and raw in ROLES:
        return raw
    return ROLE_DISPLAY_NODE


def sanitize_optional_node_id(raw: object) -> str | None:
    if isinstance(raw, str) and raw:
        return raw
    return None


@dataclass
class AppConfig:
    """运行配置；拓扑只作为运行态字段，不持久保存邻居关系。"""

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
    role: str = ROLE_DISPLAY_NODE
    admin_id: str | None = None
    # MySQL 管理配置的最后成功版本；0 表示尚未连接过数据库。
    managed_config_version: int = 0
    # 仅手动拓扑会缓存版本和邻接关系，自动拓扑每次启动重新协商。
    manual_topology_version: str | None = None
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
        self.role = sanitize_role(self.role)
        self.admin_id = sanitize_optional_node_id(self.admin_id)
        self.managed_config_version = max(0, int(self.managed_config_version))
        self.manual_topology_version = sanitize_optional_node_id(self.manual_topology_version)
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
    # 只有 MySQL 已确认的手动拓扑允许作为离线缓存恢复；自动拓扑仍每次重算。
    if not (
        raw.get("auto_topology") is False
        and sanitize_optional_node_id(raw.get("manual_topology_version"))
    ):
        merged["topology"] = _default_topology()
    # 管理员身份是局域网运行态，每次启动由发现消息重新判定。
    merged["admin_id"] = None
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


def _persistent_payload(config: AppConfig) -> dict:
    payload = config.normalized().to_dict()
    # 自动拓扑和未被 MySQL 确认的手动拓扑不写盘；已确认手动拓扑作为故障缓存。
    if config.auto_topology or not config.manual_topology_version:
        payload["topology"] = _default_topology()
    # admin_id 是当前运行态发现结果，不写入配置文件。
    payload["admin_id"] = None
    return payload


def save_config(path: Path, config: AppConfig) -> bool:
    """原子化写入配置文件。

    写入失败时保护原文件不被破坏，并返回 False（Requirement 7.7）。
    """
    payload = json.dumps(
        _persistent_payload(config),
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
