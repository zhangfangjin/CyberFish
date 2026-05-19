from __future__ import annotations

from dataclasses import dataclass
import math
import random

import pygame

from .config import AppConfig
from .fish import Fish
from .network import Peer


@dataclass
class Bubble:
    x: float
    y: float
    radius: float
    speed: float
    drift: float


@dataclass
class Ripple:
    x: float
    y: float
    radius: float
    age: float = 0.0
    duration: float = 1.2


class AquariumRenderer:
    def __init__(self, screen: pygame.Surface, rng: random.Random) -> None:
        self.screen = screen
        self.rng = rng
        self.font = pygame.font.Font(None, 22)
        self.large_font = pygame.font.Font(None, 28)
        self.bubbles: list[Bubble] = []
        self.ripples: list[Ripple] = []
        self._background: pygame.Surface | None = None
        self._background_size: tuple[int, int] | None = None
        self._seed_bubbles()

    def resize(self, screen: pygame.Surface) -> None:
        self.screen = screen
        self._background = None
        self._background_size = None
        self._seed_bubbles()

    def render(
        self,
        fishes: list[Fish],
        dt: float,
        *,
        config: AppConfig,
        peers: list[Peer],
        fps: float,
        paused: bool,
        calibration: bool,
        selected_peer: Peer | None,
    ) -> None:
        self._draw_background()
        self._update_bubbles(dt)
        self._draw_ripples(dt)
        self._draw_bubbles()
        for fish in sorted(fishes, key=lambda item: item.depth):
            self._draw_fish(fish)
        self._draw_vignette()
        self._draw_hud(config, peers, fps, len(fishes), paused, calibration, selected_peer)

    def add_ripple(self, position: pygame.Vector2, radius: float) -> None:
        self.ripples.append(Ripple(position.x, position.y, radius))

    def _seed_bubbles(self) -> None:
        width, height = self.screen.get_size()
        count = max(16, (width * height) // 70000)
        self.bubbles = [
            Bubble(
                x=self.rng.uniform(0, width),
                y=self.rng.uniform(0, height),
                radius=self.rng.uniform(1.6, 5.4),
                speed=self.rng.uniform(10.0, 38.0),
                drift=self.rng.uniform(-16.0, 16.0),
            )
            for _ in range(count)
        ]

    def _draw_background(self) -> None:
        size = self.screen.get_size()
        if self._background is None or self._background_size != size:
            width, height = size
            background = pygame.Surface(size).convert()
            for y in range(height):
                t = y / max(1, height - 1)
                top = (5, 34, 58)
                bottom = (8, 95, 118)
                color = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
                pygame.draw.line(background, color, (0, y), (width, y))
            for x in range(0, width, 46):
                shade = 18 + int(14 * math.sin(x * 0.025))
                pygame.draw.line(background, (7, shade + 45, shade + 62), (x, 0), (x - 80, height), 1)
            self._background = background
            self._background_size = size
        self.screen.blit(self._background, (0, 0))

    def _update_bubbles(self, dt: float) -> None:
        width, height = self.screen.get_size()
        for bubble in self.bubbles:
            bubble.y -= bubble.speed * dt
            bubble.x += math.sin(pygame.time.get_ticks() * 0.001 + bubble.y * 0.02) * bubble.drift * dt
            if bubble.y < -bubble.radius * 2:
                bubble.y = height + bubble.radius * 2
                bubble.x = self.rng.uniform(0, width)

    def _draw_bubbles(self) -> None:
        for bubble in self.bubbles:
            alpha = 70 + int(70 * min(1.0, bubble.radius / 5.4))
            surface = pygame.Surface((int(bubble.radius * 4), int(bubble.radius * 4)), pygame.SRCALPHA)
            center = (surface.get_width() // 2, surface.get_height() // 2)
            pygame.draw.circle(surface, (190, 242, 255, alpha), center, int(bubble.radius), 1)
            pygame.draw.circle(surface, (235, 255, 255, alpha + 25), (center[0] - 1, center[1] - 1), 1)
            self.screen.blit(surface, (bubble.x - center[0], bubble.y - center[1]))

    def _draw_ripples(self, dt: float) -> None:
        alive: list[Ripple] = []
        for ripple in self.ripples:
            ripple.age += dt
            progress = ripple.age / ripple.duration
            if progress >= 1.0:
                continue
            alive.append(ripple)
            radius = int(ripple.radius * (0.6 + progress * 1.6))
            alpha = int(90 * (1.0 - progress))
            rect = pygame.Rect(0, 0, radius * 2, max(3, int(radius * 0.5)))
            rect.center = (int(ripple.x), int(ripple.y))
            surface = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.ellipse(surface, (180, 235, 248, alpha), surface.get_rect(), 2)
            self.screen.blit(surface, rect)
        self.ripples = alive

    def _draw_fish(self, fish: Fish) -> None:
        length = max(18, int(fish.body_length))
        height = max(10, int(fish.body_height))
        canvas_size = (int(length * 1.95), int(height * 3.0))
        surface = pygame.Surface(canvas_size, pygame.SRCALPHA)
        cx = int(canvas_size[0] * 0.58)
        cy = canvas_size[1] // 2
        tail_sway = math.sin(fish.phase) * height * 0.35
        tail = [
            (cx - int(length * 0.45), cy),
            (cx - int(length * 0.92), cy - int(height * 0.64 + tail_sway)),
            (cx - int(length * 0.86), cy + int(height * 0.64 - tail_sway)),
        ]
        tail_color = tuple(max(0, min(255, int(channel * 0.92))) for channel in fish.color)
        pygame.draw.polygon(surface, (*tail_color, 200), tail)
        body_rect = pygame.Rect(0, 0, length, height)
        body_rect.center = (cx, cy)
        pygame.draw.ellipse(surface, (*fish.color, 235), body_rect)
        highlight = pygame.Rect(body_rect)
        highlight.height = max(3, int(height * 0.34))
        highlight.y += int(height * 0.12)
        pygame.draw.ellipse(surface, (255, 255, 255, 48), highlight)
        fin = [
            (cx - int(length * 0.04), cy + int(height * 0.08)),
            (cx - int(length * 0.23), cy + int(height * 0.68)),
            (cx + int(length * 0.15), cy + int(height * 0.26)),
        ]
        pygame.draw.polygon(surface, (255, 255, 255, 82), fin)
        eye_x = cx + int(length * 0.34)
        eye_radius = max(2, int(height * 0.11))
        pygame.draw.circle(surface, (245, 250, 255, 230), (eye_x, cy - int(height * 0.11)), eye_radius)
        pygame.draw.circle(surface, (20, 35, 42, 245), (eye_x + 1, cy - int(height * 0.11)), max(1, eye_radius // 2))

        angle = math.degrees(math.atan2(fish.velocity.y, fish.velocity.x))
        rotated = pygame.transform.rotozoom(surface, -angle, 1.0)
        shadow = pygame.transform.rotozoom(surface, -angle, 1.02)
        shadow.fill((0, 0, 0, 70), special_flags=pygame.BLEND_RGBA_MULT)
        shadow_rect = shadow.get_rect(center=(fish.position.x + 10 * fish.depth, fish.position.y + 12 * fish.depth))
        self.screen.blit(shadow, shadow_rect)
        rect = rotated.get_rect(center=fish.position)
        self.screen.blit(rotated, rect)

    def _draw_vignette(self) -> None:
        width, height = self.screen.get_size()
        overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        pygame.draw.rect(overlay, (0, 12, 24, 60), overlay.get_rect(), width=18)
        pygame.draw.rect(overlay, (0, 0, 0, 34), (0, height - 80, width, 80))
        self.screen.blit(overlay, (0, 0))

    def _draw_hud(
        self,
        config: AppConfig,
        peers: list[Peer],
        fps: float,
        fish_count: int,
        paused: bool,
        calibration: bool,
        selected_peer: Peer | None,
    ) -> None:
        width, _height = self.screen.get_size()
        online_peer_ids = {peer.node_id for peer in peers}

        def topology_label(direction: str) -> str:
            peer_id = config.topology.get(direction)
            if not peer_id:
                return "-"
            marker = "*" if peer_id in online_peer_ids else "?"
            return f"{peer_id[:8]}{marker}"

        topology = " ".join(
            f"{direction[0].upper()}:{topology_label(direction)}"
            for direction in ("left", "right", "up", "down")
        )
        lines = [
            f"CyberFish  FPS {fps:4.1f}  Fish {fish_count}  Peers {len(peers)}",
            f"Node {config.node_id}  Network {'ON' if config.network_enabled else 'OFF'}  Sound {'ON' if config.sound_enabled else 'OFF'}",
            f"State {'PAUSED' if paused else 'RUNNING'}  Topology {topology}",
        ]
        if calibration:
            peer_label = "none"
            if selected_peer:
                peer_label = f"{selected_peer.hostname} / {selected_peer.node_id}"
            lines.append(f"CALIBRATION  Tab selects peer: {peer_label}  Shift+Arrow assigns neighbor")
        else:
            lines.append("Space pause  R reset  L network  M sound  F11 fullscreen  C calibrate")

        panel_height = 16 + len(lines) * 22
        panel = pygame.Surface((min(width - 24, 980), panel_height), pygame.SRCALPHA)
        panel.fill((2, 18, 31, 168))
        pygame.draw.rect(panel, (111, 210, 224, 90), panel.get_rect(), 1, border_radius=8)
        for index, line in enumerate(lines):
            font = self.large_font if index == 0 else self.font
            text = font.render(line, True, (224, 248, 252))
            panel.blit(text, (12, 10 + index * 22))
        self.screen.blit(panel, (12, 12))
