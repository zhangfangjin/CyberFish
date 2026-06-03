from __future__ import annotations

from dataclasses import dataclass
import math
import random

import pygame

from .config import AppConfig
from .controls import ControlAction, ControlConsole
from .fish import Fish
from .network import Peer


@dataclass
class Bubble:
    """背景气泡，用轻量粒子增强水下运动感。"""

    x: float
    y: float
    radius: float
    speed: float
    drift: float


@dataclass
class Ripple:
    """鱼跨屏移交或接收时生成的短暂水波纹。"""

    x: float
    y: float
    radius: float
    age: float = 0.0
    duration: float = 1.2


class AquariumRenderer:
    """Pygame 渲染器：绘制背景、水泡、水波、鱼和控制台。"""

    def __init__(self, screen: pygame.Surface, rng: random.Random) -> None:
        self.screen = screen
        self.rng = rng
        self.font = pygame.font.Font(None, 22)
        self.large_font = pygame.font.Font(None, 28)
        self.bubbles: list[Bubble] = []
        self.ripples: list[Ripple] = []
        self.console = ControlConsole()
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
        selected_peer: Peer | None,
        status_message: str = "",
        debug_lines: list[str] | None = None,
    ) -> None:
        # 绘制顺序从环境到前景：背景 -> 特效 -> 鱼 -> 暗角 -> 控制台。
        self._draw_background()
        self._update_bubbles(dt)
        self._draw_ripples(dt)
        self._draw_bubbles()
        for fish in sorted(fishes, key=lambda item: item.depth):
            self._draw_fish(fish)
        self._draw_vignette()
        self.console.draw(
            self.screen,
            config=config,
            peers=peers,
            selected_peer=selected_peer,
            fps=fps,
            fish_count=len(fishes),
            paused=paused,
            status_message=status_message,
        )
        if debug_lines:
            self.console.draw_debug_overlay(self.screen, debug_lines)

    def add_ripple(self, position: pygame.Vector2, radius: float) -> None:
        self.ripples.append(Ripple(position.x, position.y, radius))

    def handle_console_click(self, position: tuple[int, int]) -> ControlAction | None:
        return self.console.handle_click(position)

    def _seed_bubbles(self) -> None:
        width, height = self.screen.get_size()
        # 按屏幕面积估算气泡数量，窗口变大后画面不会显得空。
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
            # 背景渐变只在尺寸变化时重建，避免每帧逐像素绘制影响帧率。
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
            # 用扁椭圆模拟水面/鱼身扰动，比圆形更像横向水波。
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
        # 单条鱼先画到透明画布，再整体旋转，简化头尾/鱼鳍的局部坐标计算。
        # 掉头时身体向转向方向弯成 C 形：尾巴偏移 + 摆幅放大。
        turn_intensity = fish.turn_intensity
        turn_dir = fish.turn_direction if turn_intensity > 0 else 0
        sway_amplitude = 0.35 + turn_intensity * 0.55
        tail_sway = math.sin(fish.phase) * height * sway_amplitude
        tail_offset = -turn_dir * turn_intensity * height * 0.85
        tail_tip_x = cx - int(length * (0.92 + turn_intensity * 0.18))
        tail = [
            (cx - int(length * 0.45), cy + int(tail_offset * 0.3)),
            (tail_tip_x, cy + int(tail_offset) - int(height * 0.64 + tail_sway)),
            (tail_tip_x + int(length * 0.06), cy + int(tail_offset) + int(height * 0.64 - tail_sway)),
        ]
        tail_color = tuple(max(0, min(255, int(channel * 0.92))) for channel in fish.color)
        pygame.draw.polygon(surface, (*tail_color, 200), tail)
        body_rect = pygame.Rect(0, 0, length, height)
        body_rect.center = (cx, cy + int(turn_dir * turn_intensity * height * 0.18))
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
        # 掉头时整体水平方向被压扁一点，模拟正在转身，看起来更立体。
        squash = 1.0 - 0.22 * turn_intensity
        rotated = pygame.transform.rotozoom(surface, -angle, squash if squash > 0 else 1.0)
        shadow = pygame.transform.rotozoom(surface, -angle, (squash if squash > 0 else 1.0) * 1.02)
        shadow.fill((0, 0, 0, 70), special_flags=pygame.BLEND_RGBA_MULT)
        shadow_rect = shadow.get_rect(center=(fish.position.x + 10 * fish.depth, fish.position.y + 12 * fish.depth))
        self.screen.blit(shadow, shadow_rect)
        rect = rotated.get_rect(center=fish.position)
        self.screen.blit(rotated, rect)

    def _draw_vignette(self) -> None:
        width, height = self.screen.get_size()
        overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        # 暗角压住屏幕边缘，让全屏演示时水族箱边界更明显。
        pygame.draw.rect(overlay, (0, 12, 24, 60), overlay.get_rect(), width=18)
        pygame.draw.rect(overlay, (0, 0, 0, 34), (0, height - 80, width, 80))
        self.screen.blit(overlay, (0, 0))
