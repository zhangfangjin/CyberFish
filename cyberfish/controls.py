from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pygame

from .config import AppConfig
from .network import Peer


FONT_CANDIDATES = (
    "PingFang SC",
    "Heiti SC",
    "STHeiti",
    "Hiragino Sans GB",
    "Arial Unicode MS",
    "Noto Sans CJK SC",
)


def load_ui_font(size: int) -> pygame.font.Font:
    for name in FONT_CANDIDATES:
        path = pygame.font.match_font(name)
        if path:
            return pygame.font.Font(path, size)
    return pygame.font.Font(None, size)


@dataclass(frozen=True)
class ControlAction:
    name: str
    value: Any = None


@dataclass
class ConsoleButton:
    label: str
    action: ControlAction
    rect: pygame.Rect
    enabled: bool = True
    active: bool = False


class ControlConsole:
    def __init__(self) -> None:
        self.font = load_ui_font(20)
        self.small_font = load_ui_font(18)
        self.title_font = load_ui_font(25)
        self.buttons: list[ConsoleButton] = []

    def draw(
        self,
        surface: pygame.Surface,
        *,
        config: AppConfig,
        peers: list[Peer],
        selected_peer: Peer | None,
        fps: float,
        fish_count: int,
        paused: bool,
        status_message: str = "",
    ) -> None:
        self.buttons = []
        width, height = surface.get_size()
        panel_width = min(420, max(330, width - 24))
        panel_height = min(height - 24, 392)
        panel_rect = pygame.Rect(12, 12, panel_width, panel_height)

        panel = pygame.Surface(panel_rect.size, pygame.SRCALPHA)
        panel.fill((2, 18, 31, 188))
        pygame.draw.rect(panel, (111, 210, 224, 120), panel.get_rect(), 1, border_radius=8)
        surface.blit(panel, panel_rect)

        x = panel_rect.x + 14
        y = panel_rect.y + 10
        self._draw_text(surface, "CyberFish 控制台", (x, y), self.title_font, (231, 250, 252))
        self._draw_text(
            surface,
            f"帧率 {fps:4.1f}  鱼 {fish_count}  在线主机 {len(peers)}",
            (x, y + 24),
            self.small_font,
            (185, 228, 235),
        )
        self._draw_text(
            surface,
            f"本机 {config.node_id}",
            (x, y + 43),
            self.small_font,
            (185, 228, 235),
        )

        y += 68
        self._button_row(
            surface,
            x,
            y,
            [
                ("继续" if paused else "暂停", ControlAction("toggle_pause"), paused, True),
                ("重置", ControlAction("reset"), False, True),
                ("退出", ControlAction("quit"), False, True),
            ],
        )
        y += 38
        self._button_row(
            surface,
            x,
            y,
            [
                ("网络 开" if config.network_enabled else "网络 关", ControlAction("toggle_network"), config.network_enabled, True),
                ("音效 开" if config.sound_enabled else "音效 关", ControlAction("toggle_sound"), config.sound_enabled, True),
                ("全屏" if not config.fullscreen else "窗口", ControlAction("toggle_fullscreen"), config.fullscreen, True),
            ],
        )

        y += 38
        self._button_row(
            surface,
            x,
            y,
            [
                (
                    "自动拓扑 开" if config.auto_topology else "自动拓扑 关",
                    ControlAction("toggle_auto_topology"),
                    config.auto_topology,
                    True,
                ),
            ],
            button_width=180,
        )

        y += 44
        self._draw_text(surface, f"鱼数量 {config.fish_count}", (x, y), self.font, (231, 250, 252))
        self._button(surface, pygame.Rect(x + 116, y - 5, 36, 28), "-", ControlAction("fish_dec"), enabled=config.fish_count > 1)
        self._button(surface, pygame.Rect(x + 158, y - 5, 36, 28), "+", ControlAction("fish_inc"), enabled=config.fish_count < 200)
        self._draw_text(surface, f"速度 {config.speed_multiplier:.1f}x", (x + 216, y), self.font, (231, 250, 252))
        self._button(surface, pygame.Rect(x + 314, y - 5, 36, 28), "-", ControlAction("speed_dec"), enabled=config.speed_multiplier > 0.1)
        self._button(surface, pygame.Rect(x + 356, y - 5, 36, 28), "+", ControlAction("speed_inc"), enabled=config.speed_multiplier < 4.0)

        y += 39
        topology = self._topology_text(config, peers)
        self._draw_text(surface, topology, (x, y), self.small_font, (185, 228, 235))

        y += 28
        self._draw_text(surface, "选择在线主机", (x, y), self.font, (231, 250, 252))
        y += 24
        peer_rects_bottom = self._draw_peer_buttons(surface, x, y, panel_rect.right - 14, peers, selected_peer)
        y = peer_rects_bottom + 12

        selected_label = selected_peer.node_id[:8] if selected_peer else "未选择"
        self._draw_text(surface, f"设置相邻方向: {selected_label}", (x, y), self.small_font, (185, 228, 235))
        y += 22
        direction_enabled = selected_peer is not None
        self._button_row(
            surface,
            x,
            y,
            [
                ("左", ControlAction("assign_direction", "left"), False, direction_enabled),
                ("右", ControlAction("assign_direction", "right"), False, direction_enabled),
                ("上", ControlAction("assign_direction", "up"), False, direction_enabled),
                ("下", ControlAction("assign_direction", "down"), False, direction_enabled),
            ],
            button_width=54,
        )

        if status_message:
            y += 36
            self._draw_text(surface, status_message, (x, y), self.small_font, (245, 196, 140))

    def handle_click(self, position: tuple[int, int]) -> ControlAction | None:
        for button in reversed(self.buttons):
            if button.enabled and button.rect.collidepoint(position):
                return button.action
        return None

    def _draw_peer_buttons(
        self,
        surface: pygame.Surface,
        x: int,
        y: int,
        right: int,
        peers: list[Peer],
        selected_peer: Peer | None,
    ) -> int:
        if not peers:
            self._draw_text(surface, "暂无在线主机", (x, y + 4), self.small_font, (152, 188, 194))
            return y + 30

        cursor_x = x
        cursor_y = y
        for index, peer in enumerate(peers):
            label = f"{index + 1}. {peer.hostname[:12]} {peer.node_id[:8]}"
            rect = pygame.Rect(cursor_x, cursor_y, 184, 28)
            if rect.right > right and cursor_x != x:
                cursor_x = x
                cursor_y += 34
                rect = pygame.Rect(cursor_x, cursor_y, 184, 28)
            self._button(
                surface,
                rect,
                label,
                ControlAction("select_peer", index),
                active=selected_peer is not None and peer.node_id == selected_peer.node_id,
            )
            cursor_x += rect.width + 8
        return cursor_y + 30

    def _button_row(
        self,
        surface: pygame.Surface,
        x: int,
        y: int,
        specs: list[tuple[str, ControlAction, bool, bool]],
        *,
        button_width: int = 118,
    ) -> None:
        for index, (label, action, active, enabled) in enumerate(specs):
            rect = pygame.Rect(x + index * (button_width + 8), y, button_width, 30)
            self._button(surface, rect, label, action, enabled=enabled, active=active)

    def _button(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        label: str,
        action: ControlAction,
        *,
        enabled: bool = True,
        active: bool = False,
    ) -> None:
        color = (33, 113, 132, 230) if active else (12, 50, 68, 224)
        border = (137, 230, 238, 190) if active else (96, 172, 184, 140)
        text_color = (236, 253, 255) if enabled else (118, 148, 154)
        if not enabled:
            color = (14, 31, 42, 190)
            border = (60, 84, 92, 130)
        pygame.draw.rect(surface, color, rect, border_radius=6)
        pygame.draw.rect(surface, border, rect, 1, border_radius=6)
        text = self.font.render(label, True, text_color)
        text_rect = text.get_rect(center=rect.center)
        surface.blit(text, text_rect)
        self.buttons.append(ConsoleButton(label, action, rect.copy(), enabled, active))

    def _draw_text(
        self,
        surface: pygame.Surface,
        text: str,
        position: tuple[int, int],
        font: pygame.font.Font,
        color: tuple[int, int, int],
    ) -> None:
        rendered = font.render(text, True, color)
        surface.blit(rendered, position)

    @staticmethod
    def _topology_text(config: AppConfig, peers: list[Peer]) -> str:
        online_peer_ids = {peer.node_id for peer in peers}
        parts = []
        for label, direction in (("左", "left"), ("右", "right"), ("上", "up"), ("下", "down")):
            peer_id = config.topology.get(direction)
            if not peer_id:
                value = "无邻居"
            else:
                marker = "在线" if peer_id in online_peer_ids else "离线"
                value = f"{peer_id[:8]}({marker})"
            parts.append(f"{label}:{value}")
        return "拓扑 " + "  ".join(parts)
