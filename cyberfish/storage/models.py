from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..config import AppConfig, sanitize_topology


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise ValueError(f"Expected boolean value, got {value!r}")


@dataclass(frozen=True)
class NodeOverride:
    fullscreen: bool
    display_index: int
    window_width: int
    window_height: int

    @classmethod
    def from_config(cls, config: AppConfig) -> "NodeOverride":
        return cls(
            fullscreen=bool(config.fullscreen),
            display_index=int(config.display_index),
            window_width=int(config.window_width),
            window_height=int(config.window_height),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fullscreen": self.fullscreen,
            "display_index": self.display_index,
            "window_width": self.window_width,
            "window_height": self.window_height,
        }


@dataclass(frozen=True)
class ConfigSnapshot:
    version: int
    fish_count: int
    speed_multiplier: float
    sound_enabled: bool
    network_enabled: bool
    auto_topology: bool
    node: NodeOverride
    manual_topology_id: str | None = None
    topology: dict[str, str | None] = field(default_factory=dict)
    topologies: dict[str, dict[str, str | None]] = field(default_factory=dict)
    node_overrides: dict[str, NodeOverride] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: AppConfig) -> "ConfigSnapshot":
        topology = (
            sanitize_topology(config.topology)
            if not config.auto_topology and config.manual_topology_version
            else sanitize_topology({})
        )
        local_topologies = {config.node_id: topology} if config.manual_topology_version else {}
        local_override = NodeOverride.from_config(config)
        return cls(
            version=max(0, int(config.managed_config_version)),
            fish_count=int(config.fish_count),
            speed_multiplier=float(config.speed_multiplier),
            sound_enabled=bool(config.sound_enabled),
            network_enabled=bool(config.network_enabled),
            auto_topology=bool(config.auto_topology),
            node=local_override,
            manual_topology_id=config.manual_topology_version,
            topology=topology,
            topologies=local_topologies,
            node_overrides={config.node_id: local_override},
        ).normalized()

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ConfigSnapshot":
        node_payload = payload.get("node")
        if not isinstance(node_payload, dict):
            node_payload = {}
        node = NodeOverride(
            fullscreen=_as_bool(node_payload.get("fullscreen"), False),
            display_index=max(0, int(node_payload.get("display_index", 0))),
            window_width=max(320, int(node_payload.get("window_width", 1280))),
            window_height=max(240, int(node_payload.get("window_height", 720))),
        )
        raw_topologies = payload.get("topologies")
        topologies = {
            str(node_id): sanitize_topology(value)
            for node_id, value in raw_topologies.items()
        } if isinstance(raw_topologies, dict) else {}
        raw_overrides = payload.get("node_overrides")
        node_overrides = {
            str(node_id): NodeOverride(
                fullscreen=_as_bool(value.get("fullscreen"), False),
                display_index=max(0, int(value.get("display_index", 0))),
                window_width=max(320, int(value.get("window_width", 1280))),
                window_height=max(240, int(value.get("window_height", 720))),
            )
            for node_id, value in raw_overrides.items()
            if isinstance(value, dict)
        } if isinstance(raw_overrides, dict) else {}
        return cls(
            version=max(0, int(payload.get("config_version", payload.get("version", 0)))),
            fish_count=int(payload.get("fish_count", 12)),
            speed_multiplier=float(payload.get("speed_multiplier", 1.0)),
            sound_enabled=_as_bool(payload.get("sound_enabled"), True),
            network_enabled=_as_bool(payload.get("network_enabled"), True),
            auto_topology=_as_bool(payload.get("auto_topology"), True),
            node=node,
            manual_topology_id=(
                str(payload["manual_topology_id"])
                if payload.get("manual_topology_id")
                else None
            ),
            topology=sanitize_topology(payload.get("topology")),
            topologies=topologies,
            node_overrides=node_overrides,
        ).normalized()

    def normalized(self) -> "ConfigSnapshot":
        return ConfigSnapshot(
            version=max(0, int(self.version)),
            fish_count=min(200, max(1, int(self.fish_count))),
            speed_multiplier=min(4.0, max(0.1, round(float(self.speed_multiplier), 1))),
            sound_enabled=bool(self.sound_enabled),
            network_enabled=bool(self.network_enabled),
            auto_topology=bool(self.auto_topology),
            node=NodeOverride(
                fullscreen=bool(self.node.fullscreen),
                display_index=max(0, int(self.node.display_index)),
                window_width=max(320, min(65535, int(self.node.window_width))),
                window_height=max(240, min(65535, int(self.node.window_height))),
            ),
            manual_topology_id=self.manual_topology_id or None,
            topology=sanitize_topology(self.topology),
            topologies={
                str(node_id): sanitize_topology(topology)
                for node_id, topology in self.topologies.items()
            },
            node_overrides={
                str(node_id): NodeOverride(
                    fullscreen=bool(override.fullscreen),
                    display_index=max(0, int(override.display_index)),
                    window_width=max(320, min(65535, int(override.window_width))),
                    window_height=max(240, min(65535, int(override.window_height))),
                )
                for node_id, override in self.node_overrides.items()
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_version": self.version,
            "fish_count": self.fish_count,
            "speed_multiplier": self.speed_multiplier,
            "sound_enabled": self.sound_enabled,
            "network_enabled": self.network_enabled,
            "auto_topology": self.auto_topology,
            "node": self.node.to_dict(),
            "manual_topology_id": self.manual_topology_id,
            "topology": sanitize_topology(self.topology),
            "topologies": {
                node_id: sanitize_topology(topology)
                for node_id, topology in self.topologies.items()
            },
            "node_overrides": {
                node_id: override.to_dict()
                for node_id, override in self.node_overrides.items()
            },
        }

    def with_changes(self, **changes: Any) -> "ConfigSnapshot":
        payload = self.to_dict()
        node_changes = changes.pop("node", None)
        payload.update(changes)
        if isinstance(node_changes, dict):
            payload["node"] = {**payload["node"], **node_changes}
        return ConfigSnapshot.from_dict(payload)


@dataclass(frozen=True)
class NodeRecord:
    node_id: str
    hostname: str
    role: str
    ip_address: str | None
    udp_port: int
    screen_size: tuple[int, int]
    boot_id: str
    applied_config_version: int = 0


@dataclass(frozen=True)
class MetricReport:
    node_id: str
    boot_id: str
    sequence: int
    fish_count: int
    fps: float
    counters: dict[str, int]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MetricReport":
        counters = payload.get("counters")
        if not isinstance(counters, dict):
            counters = {}
        return cls(
            node_id=str(payload.get("node_id") or ""),
            boot_id=str(payload.get("boot_id") or ""),
            sequence=max(0, int(payload.get("sequence", 0))),
            fish_count=max(0, int(payload.get("fish_count", 0))),
            fps=max(0.0, float(payload.get("fps", 0.0))),
            counters={str(key): max(0, int(value)) for key, value in counters.items()},
        )


@dataclass(frozen=True)
class StorageResult:
    kind: str
    ok: bool
    request_id: str | None = None
    snapshot: ConfigSnapshot | None = None
    message: str = ""
