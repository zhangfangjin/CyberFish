from __future__ import annotations

from pathlib import Path
import random
import time

import pygame

from .audio import AudioController
from .config import AppConfig, DIRECTIONS, load_config, save_config
from .fish import Fish, create_random_fish
from .network import NetworkManager, Peer
from .renderer import AquariumRenderer


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
        self.fishes: list[Fish] = []
        self.paused = False
        self.calibration = False
        self.selected_peer_index = 0
        self.running = False
        self._last_hello_at = 0.0
        self._last_state_at = 0.0

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
        flags = pygame.SCALED | pygame.RESIZABLE
        size = (self.config.window_width, self.config.window_height)
        if self.config.fullscreen:
            flags = pygame.FULLSCREEN | pygame.SCALED
            sizes = pygame.display.get_desktop_sizes()
            if sizes:
                index = min(self.config.display_index, len(sizes) - 1)
                size = sizes[index]
        return pygame.display.set_mode(size, flags, display=self.config.display_index)

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
            elif event.type == pygame.KEYDOWN:
                self._handle_keydown(event)

    def _handle_resize(self, size: tuple[int, int]) -> None:
        if self.config.fullscreen:
            return
        self.config.window_width, self.config.window_height = size
        if self.screen and self.renderer:
            self.screen = pygame.display.set_mode(size, pygame.SCALED | pygame.RESIZABLE)
            self.renderer.resize(self.screen)
        if self.network:
            self.network.update_screen_size(size)

    def _handle_keydown(self, event: pygame.event.Event) -> None:
        key = event.key
        if key == pygame.K_ESCAPE:
            self.running = False
        elif key == pygame.K_SPACE:
            self.paused = not self.paused
        elif key == pygame.K_r:
            self._reset_fishes()
        elif key == pygame.K_l:
            self._toggle_network()
        elif key == pygame.K_m:
            self.config.sound_enabled = not self.config.sound_enabled
            self.audio.set_enabled(self.config.sound_enabled)
            save_config(self.config_path, self.config)
        elif key == pygame.K_F11:
            self._toggle_fullscreen()
        elif key == pygame.K_c:
            self.calibration = not self.calibration
        elif key == pygame.K_TAB:
            self._select_next_peer()
        elif key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN):
            if event.mod & pygame.KMOD_SHIFT:
                self._assign_selected_peer_to_direction(key)
        elif key in (pygame.K_EQUALS, pygame.K_PLUS):
            self.config.speed_multiplier = min(4.0, self.config.speed_multiplier + 0.1)
            save_config(self.config_path, self.config)
        elif key == pygame.K_MINUS:
            self.config.speed_multiplier = max(0.1, self.config.speed_multiplier - 0.1)
            save_config(self.config_path, self.config)
        elif key == pygame.K_RIGHTBRACKET:
            self.config.fish_count = min(200, self.config.fish_count + 1)
            self.fishes.append(create_random_fish(self._bounds(), self.rng, self.config.speed_multiplier))
            save_config(self.config_path, self.config)
        elif key == pygame.K_LEFTBRACKET:
            self.config.fish_count = max(1, self.config.fish_count - 1)
            if len(self.fishes) > self.config.fish_count:
                self.fishes.pop()
            save_config(self.config_path, self.config)

    def _toggle_network(self) -> None:
        self.config.network_enabled = not self.config.network_enabled
        if self.config.network_enabled and not self.network:
            self._start_network()
        elif not self.config.network_enabled and self.network:
            self.network.close()
            self.network = None
        save_config(self.config_path, self.config)

    def _toggle_fullscreen(self) -> None:
        self.config.fullscreen = not self.config.fullscreen
        self.screen = self._create_screen()
        if self.renderer:
            self.renderer.resize(self.screen)
        if self.network:
            self.network.update_screen_size(self._bounds())
        save_config(self.config_path, self.config)

    def _select_next_peer(self) -> None:
        peers = self._peers()
        if not peers:
            self.selected_peer_index = 0
            return
        self.selected_peer_index = (self.selected_peer_index + 1) % len(peers)

    def _selected_peer(self) -> Peer | None:
        peers = self._peers()
        if not peers:
            return None
        self.selected_peer_index %= len(peers)
        return peers[self.selected_peer_index]

    def _assign_selected_peer_to_direction(self, key: int) -> None:
        peer = self._selected_peer()
        if not peer:
            return
        direction_by_key = {
            pygame.K_LEFT: "left",
            pygame.K_RIGHT: "right",
            pygame.K_UP: "up",
            pygame.K_DOWN: "down",
        }
        direction = direction_by_key.get(key)
        if direction in DIRECTIONS:
            self.config.topology[direction] = peer.node_id
            save_config(self.config_path, self.config)

    def _network_tick(self, now: float) -> None:
        if not self.network:
            return
        if now - self._last_hello_at >= 1.0:
            self.network.send_hello()
            self._last_hello_at = now
        if now - self._last_state_at >= 0.25:
            self.network.send_fish_state(len(self.fishes), self._sample_fish_state())
            self._last_state_at = now
        events = self.network.poll()
        for payload in events.transfers:
            fish = Fish.from_transfer_payload(payload, self._bounds())
            self._replace_or_add_fish(fish)
            if self.renderer:
                self.renderer.add_ripple(fish.position, fish.body_length)
        for payload in events.expired_transfers:
            fish = Fish.from_expired_transfer_payload(payload, self._bounds())
            self._replace_or_add_fish(fish)

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
        remaining: list[Fish] = []
        for fish in self.fishes:
            fish.update(dt, self.fishes, bounds, self.rng, self.config.speed_multiplier)
            direction = fish.crossed_edge(bounds)
            if direction and self._try_transfer_fish(fish, direction):
                if self.renderer:
                    self.renderer.add_ripple(fish.position, fish.body_length)
                continue
            if direction:
                fish.bounce_inside(bounds)
            remaining.append(fish)
        self.fishes = remaining

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
            calibration=self.calibration,
            selected_peer=self._selected_peer(),
        )
        pygame.display.flip()

    def _peers(self) -> list[Peer]:
        if not self.network:
            return []
        return self.network.sorted_peers()
