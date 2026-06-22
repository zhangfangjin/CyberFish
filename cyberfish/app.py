from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import random
import socket
import time
import uuid

import pygame

from .audio import AudioController
from .config import (
    AppConfig,
    DIRECTIONS,
    INVERSE_DIRECTIONS,
    ROLE_ADMIN,
    ROLE_DISPLAY_NODE,
    load_config,
    save_config,
    sanitize_role,
)
from .controls import ControlAction
from .fish import ANIMATION_TRANSFERRING, Fish, create_random_fish
from .network import NetworkManager, Peer
from .renderer import AquariumRenderer
from .storage import (
    ConfigSnapshot,
    DatabaseService,
    MetricReport,
    MySQLSettings,
    NodeOverride,
    NodeRecord,
)
from .topology import TopologyCoordinator


FISH_STATE_INTERVAL_SECONDS = 0.1
PEER_FISH_STATE_TTL_SECONDS = 0.5
NODE_METRIC_INTERVAL_SECONDS = 10.0
CONFIG_RECONCILE_INTERVAL_SECONDS = 2.0
GHOST_EDGE_MARGIN_SCALE = 1.2
TOPOLOGY_OFFSETS = {
    "left": (-1, 0),
    "right": (1, 0),
    "up": (0, -1),
    "down": (0, 1),
}


@dataclass
class PeerFishSnapshot:
    node_id: str
    sequence: int
    received_at: float
    screen_size: tuple[int, int]
    fish_count: int
    fishes: list[Fish]


class CyberFishApp:
    """应用总控：把 Pygame 渲染、鱼群物理、音频和 UDP 网络流程串在同一帧循环里。"""

    def __init__(
        self,
        config_path: Path,
        *,
        force_network_enabled: bool | None = None,
        role_override: str | None = None,
        debug_net: bool = False,
    ) -> None:
        self.config_path = config_path
        self.config: AppConfig = load_config(config_path)
        self._saved_config_role = self.config.role
        self._role_override = sanitize_role(role_override) if role_override is not None else None
        if self._role_override is not None:
            self.config.role = self._role_override
        self._network_forced_off = force_network_enabled is False
        self._network_enabled = (
            self.config.network_enabled
            if force_network_enabled is None
            else force_network_enabled
        )
        self.debug_net = debug_net
        self.rng = random.Random()
        self.clock: pygame.time.Clock | None = None
        self.screen: pygame.Surface | None = None
        self.renderer: AquariumRenderer | None = None
        self.audio = AudioController(self.config.sound_enabled)
        self.network: NetworkManager | None = None
        self.boot_id = str(uuid.uuid4())
        self.db_settings = MySQLSettings.from_env()
        self.storage: DatabaseService | None = None
        self.active_config = ConfigSnapshot.from_config(self.config)
        self._pending_config_request: str | None = None
        self._last_metric_at = 0.0
        self._last_config_reconcile_at = 0.0
        self._last_topology_signature: tuple | None = None
        self._pending_manual_topology_id: str | None = None
        self._audited_config_versions: set[int] = set()
        self.topology = TopologyCoordinator(
            node_id=self.config.node_id,
            topology=self.config.topology,
            auto_mode=self.config.auto_topology,
            now_func=time.monotonic,
        )
        self.fishes: list[Fish] = []
        self.paused = False
        self.selected_peer_index = 0
        self.running = False
        self._last_heartbeat_at = 0.0
        self._last_state_at = 0.0
        self._last_topology_at = 0.0
        self._last_topology_claim_at = 0.0
        self._last_debug_log_at = 0.0
        self.status_message: str = ""
        self.effective_role = ROLE_DISPLAY_NODE
        self.admin_conflict = False
        self.admin_ack_status: dict[str, str] = {}
        self.peer_fish_states: dict[str, PeerFishSnapshot] = {}
        self._update_role_state()

    def run(self, max_seconds: float | None = None) -> None:
        pygame.init()
        pygame.font.init()
        self.clock = pygame.time.Clock()
        self.screen = self._create_screen()
        pygame.display.set_caption("CyberFish")
        self.renderer = AquariumRenderer(self.screen, self.rng)
        self.audio.start()
        self._reset_fishes()
        self._start_network()
        self._start_storage_if_admin()
        self._render(0.0)

        self.running = True
        started_at = time.monotonic()
        try:
            while self.running:
                now = time.monotonic()
                if max_seconds is not None and now - started_at >= max_seconds:
                    break
                # 限制单帧最大 dt，窗口拖动或系统卡顿后不会让鱼瞬间跨过整屏。
                dt = min(0.05, self.clock.tick(60) / 1000.0)
                self._handle_events()
                self._network_tick(now)
                self._storage_tick(now)
                self._metric_tick(now)
                if not self.paused:
                    self._update_fishes(dt)
                self._state_sync_tick(time.monotonic())
                self._render(dt)
        finally:
            self._shutdown()

    def _create_screen(self) -> pygame.Surface:
        size = (self.config.window_width, self.config.window_height)
        if self.config.fullscreen:
            sizes = pygame.display.get_desktop_sizes()
            if sizes:
                index = min(self.config.display_index, len(sizes) - 1)
                size = sizes[index]
        return self._set_display_mode(size, fullscreen=self.config.fullscreen)

    def _set_display_mode(
        self,
        size: tuple[int, int],
        *,
        fullscreen: bool,
    ) -> pygame.Surface:
        width, height = size
        size = (max(320, int(width)), max(240, int(height)))
        flags = pygame.FULLSCREEN if fullscreen else pygame.RESIZABLE

        try:
            return pygame.display.set_mode(
                size,
                flags,
                display=self.config.display_index,
            )
        except pygame.error as exc:
            last_error: pygame.error = exc
        if self.config.display_index != 0:
            try:
                # 多显示器索引可能失效，回退到主屏保证演示程序仍能启动。
                return pygame.display.set_mode(size, flags, display=0)
            except pygame.error as exc:
                last_error = exc
        raise last_error

    def _start_network(self) -> None:
        if self._network_forced_off:
            return
        try:
            self.network = NetworkManager(
                node_id=self.config.node_id,
                listen_port=self.config.udp_port,
                broadcast_host=self.config.broadcast_host,
                screen_size=self._bounds(),
                role=self.effective_role,
                boot_id=self.boot_id,
                applied_config_version=self.config.managed_config_version,
            )
            self.network.send_node_join()
            self.network.send_hello()
        except OSError:
            # 课堂/沙箱环境可能禁止 UDP 绑定；失败时降级为单机水族箱。
            self.network = None
            self._network_enabled = False
            self.status_message = "网络启动失败，已临时降级为单机"

    def _shutdown(self) -> None:
        self.audio.stop()
        if self.storage:
            self.storage.stop()
            self.storage = None
        if self.network:
            self.network.close()
        self._save_config()
        pygame.quit()

    def _save_config(self) -> bool:
        config = self.config
        if self._role_override is not None:
            config = replace(self.config, role=self._saved_config_role, admin_id=None)
        return save_config(self.config_path, config)

    def _local_node_record(self) -> NodeRecord:
        return NodeRecord(
            node_id=self.config.node_id,
            hostname=(self.network.hostname if self.network else socket.gethostname()),
            role=self.effective_role,
            ip_address=(self.network.local_ip if self.network else None),
            udp_port=(self.network.listen_port if self.network else self.config.udp_port),
            screen_size=self._bounds(),
            boot_id=self.boot_id,
            applied_config_version=self.config.managed_config_version,
        )

    def _start_storage_if_admin(self) -> None:
        if (
            not self.db_settings.enabled
            or self.effective_role != ROLE_ADMIN
            or self.storage is not None
        ):
            return
        self.storage = DatabaseService(
            self.db_settings,
            self._local_node_record(),
            ConfigSnapshot.from_config(self.config),
        )
        self.storage.start()
        self.status_message = "MySQL 连接中，当前使用本地缓存配置"

    def _storage_tick(self, now: float) -> None:
        if not self.db_settings.enabled:
            return
        if self.effective_role != ROLE_ADMIN:
            if self.storage:
                self.storage.stop()
                self.storage = None
            return
        self._start_storage_if_admin()
        if not self.storage:
            return
        for result in self.storage.poll_results():
            if result.kind in ("bootstrap", "config") and result.ok and result.snapshot:
                self._pending_config_request = None
                self._apply_config_snapshot(result.snapshot)
                self.status_message = f"MySQL 配置 v{result.snapshot.version} 已应用"
                self._broadcast_config_snapshot()
                if result.kind == "config":
                    command_id = f"config-{result.snapshot.version}"
                    self.storage.record_command(
                        {
                            "command_id": command_id,
                            "admin_node_id": self.config.node_id,
                            "target_node_id": None,
                            "action": "apply_config_snapshot",
                            "payload": result.snapshot.to_dict(),
                            "config_version": result.snapshot.version,
                            "expected_results": len(self._peers()) + 1,
                        }
                    )
                    self._audited_config_versions.add(result.snapshot.version)
                    self.storage.record_command_result(
                        {
                            "command_id": command_id,
                            "node_id": self.config.node_id,
                            "ok": True,
                            "message": "管理员已应用",
                        }
                    )
            elif result.kind == "config" and not result.ok:
                self._pending_config_request = None
                self.status_message = f"配置未修改：{result.message}"[:120]
            elif result.kind == "health" and not result.ok:
                self.status_message = f"MySQL 不可用，使用缓存：{result.message}"[:120]
            elif (
                result.kind == "topology"
                and result.ok
                and result.request_id == self._pending_manual_topology_id
            ):
                self.config.manual_topology_version = result.request_id
                self._pending_manual_topology_id = None
                payload = self.active_config.to_dict()
                payload["manual_topology_id"] = result.request_id
                payload["topology"] = dict(self.config.topology)
                payload["topologies"] = {
                    **payload.get("topologies", {}),
                    self.config.node_id: dict(self.config.topology),
                }
                self.active_config = ConfigSnapshot.from_dict(payload)
                self._save_config()

        if now - self._last_config_reconcile_at >= CONFIG_RECONCILE_INTERVAL_SECONDS:
            self._last_config_reconcile_at = now
            self.storage.record_node(self._local_node_record())
            for peer in self._peers():
                self.storage.record_node(self._peer_node_record(peer))
                if peer.applied_config_version < self.active_config.version:
                    self._send_config_snapshot(peer)

    def _peer_node_record(self, peer: Peer) -> NodeRecord:
        return NodeRecord(
            node_id=peer.node_id,
            hostname=peer.hostname,
            role=peer.role,
            ip_address=peer.address,
            udp_port=peer.port,
            screen_size=peer.screen_size,
            boot_id=peer.boot_id or "",
            applied_config_version=peer.applied_config_version,
        )

    def _request_managed_config_change(self, reason: str, **changes: object) -> bool:
        if not self.db_settings.enabled:
            return False
        if not self.storage or not self.storage.healthy:
            self.status_message = "MySQL 不可用，配置变更已拒绝"
            return True
        if self._pending_config_request:
            self.status_message = "已有配置变更正在提交"
            return True
        candidate = self.active_config.with_changes(**changes)
        request_id = self.storage.submit_config(candidate, reason)
        if request_id is None:
            self.status_message = "配置队列已满，变更未提交"
            return True
        self._pending_config_request = request_id
        self.status_message = "配置正在提交 MySQL"
        return True

    def _apply_config_snapshot(self, snapshot: ConfigSnapshot) -> None:
        snapshot = snapshot.normalized()
        if snapshot.version < self.config.managed_config_version:
            return
        old_display = (
            self.config.fullscreen,
            self.config.display_index,
            self.config.window_width,
            self.config.window_height,
        )
        self._set_fish_count(snapshot.fish_count, persist=False)
        self.config.managed_config_version = snapshot.version
        self.config.speed_multiplier = snapshot.speed_multiplier
        self.config.sound_enabled = snapshot.sound_enabled
        self.config.network_enabled = snapshot.network_enabled
        self._network_enabled = snapshot.network_enabled and not self._network_forced_off
        self.config.auto_topology = snapshot.auto_topology
        self.config.manual_topology_version = snapshot.manual_topology_id
        self.config.fullscreen = snapshot.node.fullscreen
        self.config.display_index = snapshot.node.display_index
        self.config.window_width = snapshot.node.window_width
        self.config.window_height = snapshot.node.window_height
        self.topology.set_auto_mode(snapshot.auto_topology)
        if not snapshot.auto_topology and snapshot.manual_topology_id:
            self.config.topology.clear()
            self.config.topology.update(snapshot.topology)
        self.audio.set_enabled(snapshot.sound_enabled)
        new_display = (
            self.config.fullscreen,
            self.config.display_index,
            self.config.window_width,
            self.config.window_height,
        )
        if self.screen is not None and old_display != new_display:
            self.screen = self._create_screen()
            if self.renderer:
                self.renderer.resize(self.screen)
            if self.network:
                self.network.update_screen_size(self._bounds())
        if self.network:
            self.network.set_applied_config_version(snapshot.version)
        self.active_config = snapshot
        self._save_config()

    def _send_config_snapshot(self, peer: Peer) -> None:
        if not self.network or self.effective_role != ROLE_ADMIN:
            return
        payload = self.active_config.to_dict()
        # 数据库中没有目标节点覆盖时，展示节点保留自己的显示设置缓存。
        override = self.active_config.node_overrides.get(peer.node_id)
        if override is None:
            payload.pop("node", None)
        else:
            payload["node"] = override.to_dict()
        payload.pop("topologies", None)
        payload.pop("node_overrides", None)
        if not self.active_config.auto_topology:
            payload["topology"] = self.active_config.topologies.get(
                peer.node_id,
                {
                    "left": peer.left_neighbor,
                    "right": peer.right_neighbor,
                    "up": peer.up_neighbor,
                    "down": peer.down_neighbor,
                },
            )
        self.network.send_config_snapshot(peer, payload)

    def _broadcast_config_snapshot(self) -> None:
        for peer in self._peers():
            self._send_config_snapshot(peer)

    def _bounds(self) -> tuple[int, int]:
        if not self.screen:
            return (self.config.window_width, self.config.window_height)
        return self.screen.get_size()

    def _reset_fishes(self) -> None:
        bounds = self._bounds()
        self.fishes = [
            create_random_fish(
                bounds,
                self.rng,
                self.config.speed_multiplier,
                current_node_id=self.config.node_id,
            )
            for _ in range(self.config.fish_count)
        ]

    def _handle_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.VIDEORESIZE:
                self._handle_resize(event.size)
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                self._handle_mouse_click(event.pos)
            elif event.type == pygame.KEYDOWN:
                self._handle_keydown(event)

    def _handle_resize(self, size: tuple[int, int]) -> None:
        if self.config.fullscreen:
            return
        self.config.window_width, self.config.window_height = size
        if self.screen and self.renderer:
            self.screen = self._set_display_mode(size, fullscreen=False)
            self.renderer.resize(self.screen)
        if self.network:
            self.network.update_screen_size(size)

    def _handle_keydown(self, event: pygame.event.Event) -> None:
        if event.key == pygame.K_ESCAPE:
            self.running = False

    def _handle_mouse_click(self, position: tuple[int, int]) -> None:
        if not self.renderer:
            return
        if self.effective_role != ROLE_ADMIN:
            return
        for click_position in self._console_click_positions(position):
            action = self.renderer.handle_console_click(click_position)
            if action:
                self._handle_console_action(action)
                return

    def _console_click_positions(self, position: tuple[int, int]) -> list[tuple[int, int]]:
        """返回多个可能的点击坐标，兼容 HiDPI 和窗口逻辑/物理像素差异。"""
        candidates: list[tuple[int, int]] = []

        def add(point: tuple[int, int]) -> None:
            normalized = (int(round(point[0])), int(round(point[1])))
            if normalized not in candidates:
                candidates.append(normalized)

        add((int(position[0]), int(position[1])))
        if not self.screen:
            return candidates

        surface_width, surface_height = self.screen.get_size()
        if surface_width <= 0 or surface_height <= 0:
            return candidates

        try:
            window_width, window_height = pygame.display.get_window_size()
        except pygame.error:
            window_width = window_height = 0

        if window_width > 0 and window_height > 0 and (
            (surface_width, surface_height) != (window_width, window_height)
        ):
            add((position[0] * surface_width / window_width, position[1] * surface_height / window_height))
            add((position[0] * window_width / surface_width, position[1] * window_height / surface_height))

        # Retina / HiDPI 兜底：鼠标事件可能落在物理像素坐标，按比例尝试 0.5 / 2.0 缩放。
        for ratio in (0.5, 2.0):
            add((position[0] * ratio, position[1] * ratio))

        return candidates

    def _handle_console_action(self, action: ControlAction) -> None:
        if self.effective_role != ROLE_ADMIN:
            return
        if action.name == "toggle_network":
            target_enabled = not self._network_enabled
            if self._request_managed_config_change(
                "toggle network data plane",
                network_enabled=target_enabled,
            ):
                return
            self._send_admin_command("set_network_enabled", {"enabled": target_enabled})
            self._set_network_enabled(target_enabled)
            return

        if action.name == "toggle_pause":
            self.paused = not self.paused
            self._send_admin_command("pause" if self.paused else "resume")
        elif action.name == "reset":
            self._reset_fishes()
            self._send_admin_command("reset")
        elif action.name == "quit":
            self.running = False
        elif action.name == "toggle_auto_topology":
            if self._request_managed_config_change(
                "toggle automatic topology",
                auto_topology=not self.config.auto_topology,
            ):
                return
            self._toggle_auto_topology()
        elif action.name == "toggle_sound":
            if self._request_managed_config_change(
                "toggle sound",
                sound_enabled=not self.config.sound_enabled,
            ):
                return
            self.config.sound_enabled = not self.config.sound_enabled
            self.audio.set_enabled(self.config.sound_enabled)
            self._save_config()
            self._send_admin_command(
                "set_sound_enabled",
                {"enabled": self.config.sound_enabled},
            )
        elif action.name == "toggle_fullscreen":
            if self._request_managed_config_change(
                "toggle fullscreen",
                node={"fullscreen": not self.config.fullscreen},
            ):
                return
            self._toggle_fullscreen()
        elif action.name == "fish_inc":
            if self._request_managed_config_change(
                "increase fish count",
                fish_count=min(200, self.config.fish_count + 1),
            ):
                return
            if self._change_fish_count(1):
                self._send_admin_command("set_fish_count", {"fish_count": self.config.fish_count})
        elif action.name == "fish_dec":
            if self._request_managed_config_change(
                "decrease fish count",
                fish_count=max(1, self.config.fish_count - 1),
            ):
                return
            if self._change_fish_count(-1):
                self._send_admin_command("set_fish_count", {"fish_count": self.config.fish_count})
        elif action.name == "speed_inc":
            if self._request_managed_config_change(
                "increase speed",
                speed_multiplier=min(4.0, round(self.config.speed_multiplier + 0.1, 1)),
            ):
                return
            if self._change_speed(0.1):
                self._send_admin_command(
                    "set_speed",
                    {"speed_multiplier": self.config.speed_multiplier},
                )
        elif action.name == "speed_dec":
            if self._request_managed_config_change(
                "decrease speed",
                speed_multiplier=max(0.1, round(self.config.speed_multiplier - 0.1, 1)),
            ):
                return
            if self._change_speed(-0.1):
                self._send_admin_command(
                    "set_speed",
                    {"speed_multiplier": self.config.speed_multiplier},
                )
        elif action.name == "select_peer":
            self._select_peer_by_index(action.value)
        elif action.name == "assign_direction":
            self._assign_selected_peer_to_direction(action.value)

    def _set_fish_count(self, value: object, *, persist: bool = True) -> bool:
        try:
            target = min(200, max(1, int(value)))
        except (TypeError, ValueError):
            return False
        if target == self.config.fish_count:
            return False
        self.config.fish_count = target
        while len(self.fishes) < target:
            self.fishes.append(
                create_random_fish(
                    self._bounds(),
                    self.rng,
                    self.config.speed_multiplier,
                    current_node_id=self.config.node_id,
                )
            )
        while len(self.fishes) > target:
            self.fishes.pop()
        if persist:
            self._save_config()
        return True

    def _change_fish_count(self, delta: int) -> bool:
        return self._set_fish_count(self.config.fish_count + delta)

    def _change_speed(self, delta: float) -> bool:
        return self._set_speed(self.config.speed_multiplier + delta)

    def _set_speed(self, value: object, *, persist: bool = True) -> bool:
        try:
            target = round(min(4.0, max(0.1, float(value))), 1)
        except (TypeError, ValueError):
            return False
        if target == self.config.speed_multiplier:
            return False
        self.config.speed_multiplier = target
        if persist:
            self._save_config()
        return True

    def _set_network_enabled(self, enabled: bool, *, persist: bool = True) -> None:
        # 该开关只控制鱼状态同步和跨屏数据面；发现、配置和指标管理通道保持在线，
        # 否则关闭后管理员无法远程重新开启。
        self.config.network_enabled = bool(enabled)
        self._network_enabled = self.config.network_enabled and not self._network_forced_off
        if not self._network_forced_off and not self.network:
            self._start_network()
        if persist:
            self._save_config()

    def _toggle_auto_topology(self) -> None:
        """切换自动拓扑模式（Requirement 1.3/1.7/10.5/10.6）。"""
        self.config.auto_topology = not self.config.auto_topology
        self.topology.set_auto_mode(self.config.auto_topology)
        if self._save_config():
            self.status_message = ""
        else:
            # 持久化失败时保留内存中的模式状态并提示（Requirement 1.7）。
            self.status_message = "自动模式已切换，但配置写入失败"

    def _toggle_fullscreen(self) -> None:
        self.config.fullscreen = not self.config.fullscreen
        self.screen = self._create_screen()
        if self.renderer:
            self.renderer.resize(self.screen)
        if self.network:
            self.network.update_screen_size(self._bounds())
        self._save_config()

    def _select_peer_by_index(self, index: object) -> None:
        peers = self._peers()
        if not peers:
            self.selected_peer_index = 0
            return
        try:
            self.selected_peer_index = max(0, min(len(peers) - 1, int(index)))
        except (TypeError, ValueError):
            self.selected_peer_index = 0

    def _selected_peer(self) -> Peer | None:
        peers = self._peers()
        if not peers:
            return None
        self.selected_peer_index %= len(peers)
        return peers[self.selected_peer_index]

    def _assign_selected_peer_to_direction(self, direction: object) -> None:
        peer = self._selected_peer()
        if not peer:
            return
        if direction not in DIRECTIONS:
            return
        direction = str(direction)
        # 当前在线主机即为已知节点，登记后才能通过覆盖合法性校验（Requirement 9.5）。
        self.topology.note_known_peers(p.node_id for p in self._peers())
        # 通过协调器执行手动覆盖：在自动与手动模式下都生效，并锁定该方向不被
        # 自动协商改写（Requirement 9.1/9.2）。
        accepted, message = self.topology.set_manual_override(direction, peer.node_id)
        self.status_message = message

    def _network_tick(self, now: float) -> None:
        if not self.network:
            return
        # DISCOVER 负责启动发现；HEARTBEAT 负责运行期在线状态刷新。
        if now - self._last_heartbeat_at >= 1.0:
            self.network.send_heartbeat()
            self._last_heartbeat_at = now
        # 拓扑协商消息与 HEARTBEAT 同周期广播，限制为每秒不超过 1 轮（Requirement 11.5）。
        if self.config.auto_topology and now - self._last_topology_claim_at >= 1.0:
            self.network.send_topology_claim(self.topology.build_claim_message())
            self._last_topology_claim_at = now
        events = self.network.poll()
        self._update_role_state()
        if events.node_id_conflict:
            self.status_message = "检测到相同 node_id 的主机，请修改 config.json 的 node_id"
        for ack in events.admin_acks:
            self._handle_admin_ack(ack)
        for ack in events.config_acks:
            self._handle_config_ack(ack)
        for message in events.config_snapshots:
            self._handle_config_snapshot(message)
        for peer in events.discovered:
            if self.storage:
                self.storage.record_node(self._peer_node_record(peer))
            if self.effective_role == ROLE_ADMIN and self.active_config.version > 0:
                self._send_config_snapshot(peer)
        for metric in events.node_metrics:
            self._handle_node_metric(metric)
        for claim in events.topology_claims:
            self.topology.on_claim(claim)
        for command in events.admin_commands:
            self._handle_admin_command(command)
        for snapshot in events.fish_states:
            self._handle_peer_fish_state(snapshot, now)
        for payload in events.transfers:
            # 收到鱼后按对端给出的边缘位置重建，并用 fish_id 去重，避免重发造成重复鱼。
            fish = Fish.from_transfer_payload(payload, self._bounds())
            fish.current_node_id = self.config.node_id
            self._replace_or_add_fish(fish)
            if self.renderer:
                self.renderer.add_ripple(fish.position, fish.body_length)
        for payload in events.expired_transfers:
            # 移交超时说明对端没确认，把鱼放回本机边缘继续游，避免“丢鱼”。
            fish = Fish.from_expired_transfer_payload(payload, self._bounds())
            fish.current_node_id = self.config.node_id
            self._replace_or_add_fish(fish)
        self._drop_stale_peer_fish_states(now)
        self._topology_tick(now)
        self._update_role_state()

    def _state_sync_tick(self, now: float) -> None:
        if not self.network or not self.config.network_enabled:
            return
        if now - self._last_state_at < FISH_STATE_INTERVAL_SECONDS:
            return
        self.network.update_screen_size(self._bounds())
        self.network.send_fish_state(len(self.fishes), self._full_fish_state())
        self._last_state_at = now

    def _metric_tick(self, now: float) -> None:
        if not self.network or now - self._last_metric_at < NODE_METRIC_INTERVAL_SECONDS:
            return
        fps = self.clock.get_fps() if self.clock else 0.0
        sequence = self.network.send_node_metrics(len(self.fishes), fps)
        self._last_metric_at = now
        if self.effective_role == ROLE_ADMIN and self.storage:
            self.storage.record_metric(
                MetricReport(
                    node_id=self.config.node_id,
                    boot_id=self.boot_id,
                    sequence=sequence,
                    fish_count=len(self.fishes),
                    fps=fps,
                    counters={
                        key: int(self.network.stats.get(key, 0))
                        for key in (
                            "transfer_sent",
                            "transfer_recv",
                            "ack_recv",
                            "transfer_expired",
                            "datagrams_recv",
                            "send_errors",
                        )
                    },
                )
            )

    def _handle_node_metric(self, payload: dict) -> None:
        if self.effective_role != ROLE_ADMIN or not self.storage:
            return
        try:
            report = MetricReport.from_dict(payload)
        except (TypeError, ValueError):
            return
        if report.node_id and report.node_id != self.config.node_id:
            self.storage.record_metric(report)

    def _handle_config_snapshot(self, message: dict) -> None:
        if self.effective_role == ROLE_ADMIN:
            return
        sender = str(message.get("node_id") or "")
        address = message.get("_address")
        raw_config = message.get("config")
        if not sender or sender != self.config.admin_id or not isinstance(raw_config, dict):
            return
        peer = self.network.get_peer(sender) if self.network else None
        if peer is None or peer.role != ROLE_ADMIN:
            return
        payload = dict(raw_config)
        payload.setdefault("node", NodeOverride.from_config(self.config).to_dict())
        try:
            snapshot = ConfigSnapshot.from_dict(payload)
            self._apply_config_snapshot(snapshot)
            ok = True
            response = "配置已应用"
        except (TypeError, ValueError) as exc:
            snapshot = ConfigSnapshot.from_config(self.config)
            ok = False
            response = f"配置非法: {exc}"
        if self.network and isinstance(address, tuple):
            self.network.send_config_ack(
                address,
                sender,
                snapshot.version,
                ok=ok,
                message=response,
                node_config=NodeOverride.from_config(self.config).to_dict(),
            )

    def _handle_config_ack(self, ack: dict) -> None:
        if self.effective_role != ROLE_ADMIN:
            return
        node_id = str(ack.get("node_id") or "")
        try:
            version = max(0, int(ack.get("config_version", 0)))
        except (TypeError, ValueError):
            return
        peer = self.network.get_peer(node_id) if self.network else None
        if peer and ack.get("ok"):
            peer.applied_config_version = max(peer.applied_config_version, version)
        raw_override = ack.get("node")
        if (
            self.storage
            and node_id
            and version == self.active_config.version
            and ack.get("ok")
            and isinstance(raw_override, dict)
        ):
            try:
                override = ConfigSnapshot.from_dict(
                    {**self.active_config.to_dict(), "node": raw_override}
                ).node
            except (TypeError, ValueError):
                override = None
            if override is not None:
                if peer:
                    self.storage.record_node(self._peer_node_record(peer))
                self.storage.record_node_override(node_id, version, override)
                payload = self.active_config.to_dict()
                payload["node_overrides"] = {
                    **payload.get("node_overrides", {}),
                    node_id: override.to_dict(),
                }
                self.active_config = ConfigSnapshot.from_dict(payload)
        prefix = "OK" if ack.get("ok") else "失败"
        self.admin_ack_status[node_id] = f"{prefix}: 配置 v{version}"[:40]
        if self.storage and node_id and version in self._audited_config_versions:
            self.storage.record_command_result(
                {
                    "command_id": f"config-{version}",
                    "node_id": node_id,
                    "ok": bool(ack.get("ok")),
                    "message": str(ack.get("message") or ""),
                }
            )

    def _update_role_state(self) -> None:
        admin_candidates = []
        if self.config.role == ROLE_ADMIN:
            admin_candidates.append(self.config.node_id)
        if self.network:
            admin_candidates.extend(
                peer.node_id for peer in self.network.sorted_peers() if peer.role == ROLE_ADMIN
            )
        admin_candidates = sorted(set(admin_candidates))
        self.config.admin_id = admin_candidates[0] if admin_candidates else None
        self.admin_conflict = len(admin_candidates) > 1
        self.effective_role = (
            ROLE_ADMIN
            if self.config.role == ROLE_ADMIN and self.config.admin_id == self.config.node_id
            else ROLE_DISPLAY_NODE
        )
        if self.network:
            self.network.set_role(self.effective_role)
        if self.admin_conflict and self.config.role == ROLE_ADMIN and self.effective_role != ROLE_ADMIN:
            self.status_message = "管理员冲突，已降级为演示节点"

    def _send_admin_command(
        self,
        action: str,
        payload: dict | None = None,
        *,
        target: str = "all",
    ) -> None:
        if self.effective_role != ROLE_ADMIN or not self.network:
            return
        self.network.set_role(ROLE_ADMIN)
        command_id = self.network.send_admin_command(action, payload, target=target)
        if self.storage:
            for peer in self._peers():
                self.storage.record_node(self._peer_node_record(peer))
            self.storage.record_command(
                {
                    "command_id": command_id,
                    "admin_node_id": self.config.node_id,
                    "target_node_id": None if target == "all" else target,
                    "action": action,
                    "payload": payload or {},
                    "config_version": self.config.managed_config_version or None,
                    "expected_results": len(self._peers()) + 1 if target == "all" else 1,
                }
            )
            if target == "all":
                self.storage.record_command_result(
                    {
                        "command_id": command_id,
                        "node_id": self.config.node_id,
                        "ok": True,
                        "message": "管理员本机已执行",
                    }
                )
        for peer in self._peers():
            self.admin_ack_status[peer.node_id] = f"{command_id[-6:]} 等待"

    def _handle_admin_ack(self, ack: dict) -> None:
        if self.effective_role != ROLE_ADMIN:
            return
        node_id = str(ack.get("node_id") or "")
        if not node_id:
            return
        prefix = "OK" if ack.get("ok") else "失败"
        message = str(ack.get("message") or "")
        self.admin_ack_status[node_id] = f"{prefix}: {message}"[:40]
        if self.storage:
            self.storage.record_command_result(
                {
                    "command_id": str(ack.get("command_id") or ""),
                    "node_id": node_id,
                    "ok": bool(ack.get("ok")),
                    "message": message,
                }
            )

    def _handle_admin_command(self, command: dict) -> None:
        address = command.get("_address")
        if self._should_ack_before_network_disable(command):
            if self.network and isinstance(address, tuple):
                self.network.send_admin_ack(
                    address,
                    str(command.get("command_id") or ""),
                    ok=True,
                    message="网络状态已更新",
                )
            self._set_network_enabled(False, persist=True)
            return
        ok, message = self._execute_admin_command(command)
        if self.network and isinstance(address, tuple):
            self.network.send_admin_ack(
                address,
                str(command.get("command_id") or ""),
                ok=ok,
                message=message,
            )

    def _should_ack_before_network_disable(self, command: dict) -> bool:
        if not self._is_authorized_admin_command(command):
            return False
        if command.get("action") != "set_network_enabled":
            return False
        payload = command.get("payload")
        return isinstance(payload, dict) and payload.get("enabled") is False

    def _execute_admin_command(self, command: dict) -> tuple[bool, str]:
        if not self._is_authorized_admin_command(command):
            return False, "非当前管理员命令"
        action = str(command.get("action") or "")
        payload = command.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        if action == "pause":
            self.paused = True
            return True, "已暂停"
        if action == "resume":
            self.paused = False
            return True, "已继续"
        if action == "reset":
            self._reset_fishes()
            return True, "已重置"
        if action == "set_fish_count":
            return self._command_result(
                self._set_fish_count(payload.get("fish_count"), persist=True),
                "鱼数量已更新",
            )
        if action == "set_speed":
            return self._command_result(
                self._set_speed(payload.get("speed_multiplier"), persist=True),
                "速度已更新",
            )
        if action == "set_sound_enabled":
            enabled = bool(payload.get("enabled"))
            self.config.sound_enabled = enabled
            self.audio.set_enabled(enabled)
            self._save_config()
            return True, "音效已更新"
        if action == "set_network_enabled":
            self._set_network_enabled(bool(payload.get("enabled")), persist=True)
            return True, "网络状态已更新"
        if action == "set_topology":
            return self._apply_topology_command(payload)
        return False, f"未知命令: {action}"

    def _command_result(self, changed: bool, message: str) -> tuple[bool, str]:
        return (True, message) if changed else (False, "命令无变化或参数非法")

    def _apply_topology_command(self, payload: dict) -> tuple[bool, str]:
        direction = payload.get("direction")
        peer_id = payload.get("peer_id")
        if direction not in DIRECTIONS or not isinstance(peer_id, str) or not peer_id:
            return False, "拓扑参数非法"
        self.topology.note_known_peers([peer_id])
        accepted, message = self.topology.set_manual_override(str(direction), peer_id)
        return accepted, message

    def _is_authorized_admin_command(self, command: dict) -> bool:
        sender = str(command.get("node_id") or "")
        admin_id = str(command.get("admin_id") or sender)
        if not sender or sender != admin_id or admin_id != self.config.admin_id:
            return False
        if sender == self.config.node_id:
            return self.effective_role == ROLE_ADMIN
        peer = self.network.get_peer(sender) if self.network else None
        return peer is not None and peer.role == ROLE_ADMIN

    def _topology_tick(self, now: float) -> None:
        """驱动拓扑协调器并在拓扑变化时持久化（Requirement 1.4/1.5/6.3/7.1）。"""
        if not self.network:
            return
        # 每帧重算成本很低，可让方向变化在 1 秒内反映（Requirement 1.5）。
        online_ids = {peer.node_id for peer in self.network.sorted_peers()}
        self.topology.update(online_ids, now=now)
        self._last_topology_at = now
        self._sync_peer_topology_snapshots()
        self._persist_topology_if_stable(now)
        if self.debug_net and now - self._last_debug_log_at >= 2.0:
            self._last_debug_log_at = now
            print("[debug-net] " + " | ".join(self.network.debug_lines()), flush=True)

    def _persist_topology_if_stable(self, now: float) -> None:
        if not self.storage or not self.storage.healthy or not self.topology.is_converged(now):
            return
        edges = self._topology_edges()
        mode = "auto" if self.config.auto_topology else "manual"
        signature = (mode, tuple(sorted(edges)))
        if signature == self._last_topology_signature:
            return
        for peer in self._peers():
            self.storage.record_node(self._peer_node_record(peer))
        topology_id = self.storage.record_topology(mode, True, edges)
        if topology_id is None:
            return
        if mode == "manual":
            self._pending_manual_topology_id = topology_id
        self._last_topology_signature = signature

    def _topology_edges(self) -> list[tuple[str, str, str]]:
        edges: set[tuple[str, str, str]] = set()
        for direction, target in self.config.topology.items():
            if target:
                edges.add((self.config.node_id, direction, target))
        for peer_id, claim in self.topology.peer_claims.items():
            for direction, target in claim.topology.items():
                if target:
                    edges.add((peer_id, direction, target))
        return sorted(edges)

    def _sync_peer_topology_snapshots(self) -> None:
        if not self.network:
            return
        peers = {peer.node_id: peer for peer in self.network.sorted_peers()}
        positions = self._relative_peer_positions(set(peers))
        for peer_id, peer in peers.items():
            position = positions.get(peer_id)
            if position is None:
                peer.position_x = None
                peer.position_y = None
            else:
                peer.position_x, peer.position_y = position

            claim = self.topology.peer_claims.get(peer_id)
            peer_topology = claim.topology if claim is not None else {}
            peer.left_neighbor = peer_topology.get("left")
            peer.right_neighbor = peer_topology.get("right")
            peer.up_neighbor = peer_topology.get("up")
            peer.down_neighbor = peer_topology.get("down")

            for direction, local_neighbor in self.config.topology.items():
                if local_neighbor != peer_id:
                    continue
                inverse = INVERSE_DIRECTIONS[direction]
                field_name = f"{inverse}_neighbor"
                if getattr(peer, field_name) is None:
                    setattr(peer, field_name, self.config.node_id)
            peer.online_status = True

    def _relative_peer_positions(self, online_peer_ids: set[str]) -> dict[str, tuple[int, int]]:
        positions: dict[str, tuple[int, int]] = {self.config.node_id: (0, 0)}
        for direction, peer_id in self.config.topology.items():
            if peer_id in online_peer_ids and direction in TOPOLOGY_OFFSETS:
                positions[peer_id] = TOPOLOGY_OFFSETS[direction]

        for peer_id in online_peer_ids:
            claim = self.topology.peer_claims.get(peer_id)
            if claim is None:
                continue
            for direction, neighbor_id in claim.topology.items():
                if neighbor_id != self.config.node_id or direction not in INVERSE_DIRECTIONS:
                    continue
                positions.setdefault(peer_id, TOPOLOGY_OFFSETS[INVERSE_DIRECTIONS[direction]])

        changed = True
        while changed:
            changed = False
            for peer_id, base_position in list(positions.items()):
                claim = self.topology.peer_claims.get(peer_id)
                if claim is None:
                    continue
                for direction, neighbor_id in claim.topology.items():
                    if (
                        neighbor_id not in online_peer_ids
                        or neighbor_id in positions
                        or direction not in TOPOLOGY_OFFSETS
                    ):
                        continue
                    dx, dy = TOPOLOGY_OFFSETS[direction]
                    positions[neighbor_id] = (base_position[0] + dx, base_position[1] + dy)
                    changed = True

        return {
            peer_id: position
            for peer_id, position in positions.items()
            if peer_id in online_peer_ids
        }

    def _full_fish_state(self) -> list[dict]:
        bounds = self._bounds()
        for fish in self.fishes:
            fish.current_node_id = self.config.node_id
        return [fish.to_state_payload(bounds) for fish in self.fishes]

    def _handle_peer_fish_state(self, snapshot: dict, now: float) -> None:
        node_id = str(snapshot.get("node_id") or "")
        if not node_id or node_id == self.config.node_id:
            return
        try:
            sequence = int(snapshot.get("sequence"))
        except (TypeError, ValueError):
            return
        existing = self.peer_fish_states.get(node_id)
        if existing is not None and sequence <= existing.sequence:
            return

        raw_fishes = snapshot.get("fishes")
        if not isinstance(raw_fishes, list):
            return
        screen_size = self._peer_snapshot_size(snapshot, node_id)
        fishes = []
        for payload in raw_fishes:
            if not isinstance(payload, dict):
                continue
            fish = Fish.from_state_payload(payload, screen_size)
            if fish.current_node_id is None:
                fish.current_node_id = node_id
            fishes.append(fish)
        try:
            fish_count = int(snapshot.get("fish_count", len(fishes)))
        except (TypeError, ValueError):
            fish_count = len(fishes)
        self.peer_fish_states[node_id] = PeerFishSnapshot(
            node_id=node_id,
            sequence=sequence,
            received_at=now,
            screen_size=screen_size,
            fish_count=max(0, fish_count),
            fishes=fishes,
        )

    def _peer_snapshot_size(self, snapshot: dict, node_id: str) -> tuple[int, int]:
        raw_size = snapshot.get("screen_size") or [0, 0]
        try:
            width = int(raw_size[0])
            height = int(raw_size[1])
        except (TypeError, ValueError, IndexError):
            width = height = 0
        if width > 0 and height > 0:
            return (width, height)
        peer = self.network.get_peer(node_id) if self.network else None
        if peer and peer.screen_size[0] > 0 and peer.screen_size[1] > 0:
            return peer.screen_size
        return self._bounds()

    def _drop_stale_peer_fish_states(self, now: float) -> None:
        online_ids = {peer.node_id for peer in self.network.sorted_peers()} if self.network else set()
        stale = [
            node_id
            for node_id, snapshot in self.peer_fish_states.items()
            if now - snapshot.received_at > PEER_FISH_STATE_TTL_SECONDS
            or (online_ids and node_id not in online_ids)
        ]
        for node_id in stale:
            self.peer_fish_states.pop(node_id, None)

    def _update_fishes(self, dt: float) -> None:
        bounds = self._bounds()
        open_edges = self._transfer_ready_edges()
        remaining: list[Fish] = []
        for fish in self.fishes:
            fish.update(
                dt,
                self.fishes,
                bounds,
                self.rng,
                self.config.speed_multiplier,
                open_edges=open_edges,
            )
            transfer_direction = fish.crossed_edge(
                bounds,
                margin_scale=0.12,
                only_edges=open_edges,
            )
            # 已配置且在线的边界才允许移交；发送成功后本机移除该鱼，接收端负责接续。
            if transfer_direction and self._try_transfer_fish(fish, transfer_direction):
                if self.renderer:
                    self.renderer.add_ripple(fish.position, fish.body_length)
                continue
            if transfer_direction:
                # 开放边界一度可用但发送失败时，立即弹回，避免鱼游出屏幕后消失。
                fish.bounce_inside(bounds)

            direction = fish.crossed_edge(bounds)
            if direction:
                fish.bounce_inside(bounds)
            remaining.append(fish)
        self.fishes = remaining

    def _transfer_ready_edges(self) -> set[str]:
        if not self._network_enabled or not self.network:
            return set()
        return {
            direction
            for direction, peer_id in self.config.topology.items()
            if direction in DIRECTIONS and self.network.get_peer(peer_id) is not None
        }

    def _try_transfer_fish(self, fish: Fish, direction: str) -> bool:
        if not self._network_enabled or not self.network:
            return False
        peer_id = self.config.topology.get(direction)
        peer = self.network.get_peer(peer_id)
        if not peer:
            return False
        fish.current_node_id = self.config.node_id
        fish.animation_state = ANIMATION_TRANSFERRING
        fish.is_transferring = True
        payload = fish.to_transfer_payload(direction, self._bounds())
        self.network.send_fish_transfer(peer, payload)
        return True

    def _replace_or_add_fish(self, fish: Fish) -> None:
        # 使用 fish_id 覆盖旧实例，可抵御 UDP 重试带来的重复 fish_transfer。
        self.fishes = [existing for existing in self.fishes if existing.fish_id != fish.fish_id]
        self.fishes.append(fish)

    def _render_fishes(self, now: float | None = None) -> list[Fish]:
        return [*self.fishes, *self._adjacent_edge_ghosts(now)]

    def _adjacent_edge_ghosts(self, now: float | None = None) -> list[Fish]:
        if not self.network:
            return []
        now = time.monotonic() if now is None else now
        bounds = self._bounds()
        local_ids = {fish.fish_id for fish in self.fishes}
        ghosts: list[Fish] = []
        for direction in DIRECTIONS:
            peer_id = self.config.topology.get(direction)
            if not peer_id or self.network.get_peer(peer_id) is None:
                continue
            snapshot = self.peer_fish_states.get(peer_id)
            if snapshot is None or now - snapshot.received_at > PEER_FISH_STATE_TTL_SECONDS:
                continue
            for fish in snapshot.fishes:
                if fish.fish_id in local_ids:
                    continue
                ghost = self._ghost_from_peer_fish(fish, direction, snapshot.screen_size, bounds)
                if ghost is not None:
                    ghosts.append(ghost)
        return ghosts

    def _ghost_from_peer_fish(
        self,
        fish: Fish,
        direction: str,
        peer_size: tuple[int, int],
        bounds: tuple[int, int],
    ) -> Fish | None:
        if direction not in INVERSE_DIRECTIONS:
            return None
        width, height = bounds
        peer_width = max(1, peer_size[0])
        peer_height = max(1, peer_size[1])
        normalized_x = fish.position.x / peer_width
        normalized_y = fish.position.y / peer_height
        local_x = normalized_x * width
        local_y = normalized_y * height
        if direction == "right":
            local_x = width + normalized_x * width
        elif direction == "left":
            local_x = (normalized_x - 1.0) * width
        elif direction == "down":
            local_y = height + normalized_y * height
        elif direction == "up":
            local_y = (normalized_y - 1.0) * height
        ghost = fish.copy_for_render(pygame.Vector2(local_x, local_y))
        return ghost if self._ghost_near_edge(ghost, direction, bounds) else None

    def _ghost_near_edge(self, fish: Fish, direction: str, bounds: tuple[int, int]) -> bool:
        width, height = bounds
        margin = max(32.0, fish.body_length * GHOST_EDGE_MARGIN_SCALE)
        if direction in ("left", "right") and not (-margin <= fish.position.y <= height + margin):
            return False
        if direction in ("up", "down") and not (-margin <= fish.position.x <= width + margin):
            return False
        if direction == "right":
            return width - margin <= fish.position.x <= width + margin
        if direction == "left":
            return -margin <= fish.position.x <= margin
        if direction == "down":
            return height - margin <= fish.position.y <= height + margin
        if direction == "up":
            return -margin <= fish.position.y <= margin
        return False

    def _render(self, dt: float) -> None:
        if not self.screen or not self.renderer or not self.clock:
            return
        render_config = replace(self.config, network_enabled=self._network_enabled)
        self.renderer.render(
            self._render_fishes(),
            dt,
            config=render_config,
            peers=self._peers(),
            fps=self.clock.get_fps(),
            paused=self.paused,
            local_fish_count=len(self.fishes),
            selected_peer=self._selected_peer(),
            status_message=self.status_message,
            effective_role=self.effective_role,
            admin_id=self.config.admin_id,
            admin_conflict=self.admin_conflict,
            admin_ack_status=self.admin_ack_status,
            debug_lines=self.network.debug_lines() if (self.debug_net and self.network) else None,
        )
        pygame.display.flip()

    def _peers(self) -> list[Peer]:
        if not self.network:
            return []
        return self.network.sorted_peers()
