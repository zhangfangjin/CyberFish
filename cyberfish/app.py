from __future__ import annotations

from pathlib import Path
import random
import time

import pygame

from .audio import AudioController
from .config import (
    AppConfig,
    DIRECTIONS,
    load_config,
    save_config,
    topology_equal,
)
from .controls import ControlAction
from .fish import Fish, create_random_fish
from .network import NetworkManager, Peer
from .renderer import AquariumRenderer
from .topology import TopologyCoordinator


class CyberFishApp:
    def __init__(
        self,
        config_path: Path,
        *,
        force_network_enabled: bool | None = None,
    ) -> None:
        self.config_path = config_path
        self.config: AppConfig = load_config(config_path)
        if force_network_enabled is not None:
            self.config.network_enabled = force_network_enabled
        self.rng = random.Random()
        self.clock: pygame.time.Clock | None = None
        self.screen: pygame.Surface | None = None
        self.renderer: AquariumRenderer | None = None
        self.audio = AudioController(self.config.sound_enabled)
        self.network: NetworkManager | None = None
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
        self._last_hello_at = 0.0
        self._last_state_at = 0.0
        self._last_topology_at = 0.0
        self._last_topology_claim_at = 0.0
        self.status_message: str = ""
        self._persisted_topology: dict[str, str | None] = dict(self.config.topology)

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
        self._render(0.0)

        self.running = True
        started_at = time.monotonic()
        try:
            while self.running:
                now = time.monotonic()
                if max_seconds is not None and now - started_at >= max_seconds:
                    break
                dt = min(0.05, self.clock.tick(60) / 1000.0)
                self._handle_events()
                self._network_tick(now)
                if not self.paused:
                    self._update_fishes(dt)
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
                return pygame.display.set_mode(size, flags, display=0)
            except pygame.error as exc:
                last_error = exc
        raise last_error

    def _start_network(self) -> None:
        if not self.config.network_enabled:
            return
        try:
            self.network = NetworkManager(
                node_id=self.config.node_id,
                listen_port=self.config.udp_port,
                broadcast_host=self.config.broadcast_host,
                screen_size=self._bounds(),
            )
            self.network.send_hello()
        except OSError:
            self.network = None
            self.config.network_enabled = False

    def _shutdown(self) -> None:
        self.audio.stop()
        if self.network:
            self.network.close()
        save_config(self.config_path, self.config)
        pygame.quit()

    def _bounds(self) -> tuple[int, int]:
        if not self.screen:
            return (self.config.window_width, self.config.window_height)
        return self.screen.get_size()

    def _reset_fishes(self) -> None:
        bounds = self._bounds()
        self.fishes = [
            create_random_fish(bounds, self.rng, self.config.speed_multiplier)
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
        for click_position in self._console_click_positions(position):
            action = self.renderer.handle_console_click(click_position)
            if action:
                self._handle_console_action(action)
                return

    def _console_click_positions(self, position: tuple[int, int]) -> list[tuple[int, int]]:
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
        if action.name == "toggle_pause":
            self.paused = not self.paused
        elif action.name == "reset":
            self._reset_fishes()
        elif action.name == "quit":
            self.running = False
        elif action.name == "toggle_network":
            self._toggle_network()
        elif action.name == "toggle_auto_topology":
            self._toggle_auto_topology()
        elif action.name == "toggle_sound":
            self.config.sound_enabled = not self.config.sound_enabled
            self.audio.set_enabled(self.config.sound_enabled)
            save_config(self.config_path, self.config)
        elif action.name == "toggle_fullscreen":
            self._toggle_fullscreen()
        elif action.name == "fish_inc":
            self._change_fish_count(1)
        elif action.name == "fish_dec":
            self._change_fish_count(-1)
        elif action.name == "speed_inc":
            self._change_speed(0.1)
        elif action.name == "speed_dec":
            self._change_speed(-0.1)
        elif action.name == "select_peer":
            self._select_peer_by_index(action.value)
        elif action.name == "assign_direction":
            self._assign_selected_peer_to_direction(action.value)

    def _change_fish_count(self, delta: int) -> None:
        target = min(200, max(1, self.config.fish_count + delta))
        if target == self.config.fish_count:
            return
        self.config.fish_count = target
        while len(self.fishes) < target:
            self.fishes.append(create_random_fish(self._bounds(), self.rng, self.config.speed_multiplier))
        while len(self.fishes) > target:
            self.fishes.pop()
        save_config(self.config_path, self.config)

    def _change_speed(self, delta: float) -> None:
        self.config.speed_multiplier = round(
            min(4.0, max(0.1, self.config.speed_multiplier + delta)),
            1,
        )
        save_config(self.config_path, self.config)

    def _toggle_network(self) -> None:
        self.config.network_enabled = not self.config.network_enabled
        if self.config.network_enabled and not self.network:
            self._start_network()
        elif not self.config.network_enabled and self.network:
            self.network.close()
            self.network = None
        save_config(self.config_path, self.config)

    def _toggle_auto_topology(self) -> None:
        """切换自动拓扑模式（Requirement 1.3/1.7/10.5/10.6）。"""
        self.config.auto_topology = not self.config.auto_topology
        self.topology.set_auto_mode(self.config.auto_topology)
        if save_config(self.config_path, self.config):
            self._persisted_topology = dict(self.config.topology)
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
        save_config(self.config_path, self.config)

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
        if accepted:
            self._persist_config()

    def _network_tick(self, now: float) -> None:
        if not self.network:
            return
        if now - self._last_hello_at >= 1.0:
            self.network.send_hello()
            self._last_hello_at = now
        if now - self._last_state_at >= 0.25:
            self.network.send_fish_state(len(self.fishes), self._sample_fish_state())
            self._last_state_at = now
        # 拓扑协商消息与 hello 同周期广播，限制为每秒不超过 1 轮（Requirement 11.5）。
        if self.config.auto_topology and now - self._last_topology_claim_at >= 1.0:
            self.network.send_topology_claim(self.topology.build_claim_message())
            self._last_topology_claim_at = now
        events = self.network.poll()
        for claim in events.topology_claims:
            self.topology.on_claim(claim)
        for payload in events.transfers:
            fish = Fish.from_transfer_payload(payload, self._bounds())
            self._replace_or_add_fish(fish)
            if self.renderer:
                self.renderer.add_ripple(fish.position, fish.body_length)
        for payload in events.expired_transfers:
            fish = Fish.from_expired_transfer_payload(payload, self._bounds())
            self._replace_or_add_fish(fish)
        self._topology_tick(now)

    def _topology_tick(self, now: float) -> None:
        """驱动拓扑协调器并在拓扑变化时持久化（Requirement 1.4/1.5/6.3/7.1）。"""
        if not self.network:
            return
        # 每帧重算成本很低，可让方向变化在 1 秒内反映（Requirement 1.5）。
        online_ids = {peer.node_id for peer in self.network.sorted_peers()}
        changed = self.topology.update(online_ids, now=now)
        self._last_topology_at = now
        if changed and not topology_equal(self.config.topology, self._persisted_topology):
            self._persist_config()

    def _persist_config(self) -> None:
        """持久化配置；失败时保留内存状态并提示（Requirement 6.4/7.7）。"""
        if save_config(self.config_path, self.config):
            self._persisted_topology = dict(self.config.topology)
        else:
            self.status_message = "配置写入失败，已保留内存中的拓扑"

    def _sample_fish_state(self) -> list[dict]:
        sample = []
        width, height = self._bounds()
        for fish in self.fishes[:8]:
            sample.append(
                {
                    "fish_id": fish.fish_id,
                    "x": fish.position.x / max(1, width),
                    "y": fish.position.y / max(1, height),
                    "vx": fish.velocity.x,
                    "vy": fish.velocity.y,
                }
            )
        return sample

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
            if transfer_direction and self._try_transfer_fish(fish, transfer_direction):
                if self.renderer:
                    self.renderer.add_ripple(fish.position, fish.body_length)
                continue
            if transfer_direction:
                fish.bounce_inside(bounds)

            direction = fish.crossed_edge(bounds)
            if direction:
                fish.bounce_inside(bounds)
            remaining.append(fish)
        self.fishes = remaining

    def _transfer_ready_edges(self) -> set[str]:
        if not self.config.network_enabled or not self.network:
            return set()
        return {
            direction
            for direction, peer_id in self.config.topology.items()
            if direction in DIRECTIONS and self.network.get_peer(peer_id) is not None
        }

    def _try_transfer_fish(self, fish: Fish, direction: str) -> bool:
        if not self.config.network_enabled or not self.network:
            return False
        peer_id = self.config.topology.get(direction)
        peer = self.network.get_peer(peer_id)
        if not peer:
            return False
        payload = fish.to_transfer_payload(direction, self._bounds())
        self.network.send_fish_transfer(peer, payload)
        return True

    def _replace_or_add_fish(self, fish: Fish) -> None:
        self.fishes = [existing for existing in self.fishes if existing.fish_id != fish.fish_id]
        self.fishes.append(fish)

    def _render(self, dt: float) -> None:
        if not self.screen or not self.renderer or not self.clock:
            return
        self.renderer.render(
            self.fishes,
            dt,
            config=self.config,
            peers=self._peers(),
            fps=self.clock.get_fps(),
            paused=self.paused,
            selected_peer=self._selected_peer(),
            status_message=self.status_message,
        )
        pygame.display.flip()

    def _peers(self) -> list[Peer]:
        if not self.network:
            return []
        return self.network.sorted_peers()
