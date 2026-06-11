from __future__ import annotations

import unittest

import pygame

from cyberfish.config import ROLE_ADMIN, ROLE_DISPLAY_NODE, AppConfig
from cyberfish.controls import ControlConsole
from cyberfish.network import Peer


class ControlConsoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pygame.font.init()

    @staticmethod
    def make_peer(node_id: str = "node-b", role: str = ROLE_DISPLAY_NODE) -> Peer:
        return Peer(node_id, "host", "127.0.0.1", 37777, (800, 600), 0.0, role=role)

    def test_draw_creates_clickable_chinese_controls(self) -> None:
        console = ControlConsole()
        surface = pygame.Surface((900, 700))
        config = AppConfig(node_id="node-a", role=ROLE_ADMIN, admin_id="node-a")
        peer = Peer(
            "node-b",
            "host-b",
            "127.0.0.1",
            37777,
            (800, 600),
            0.0,
            role=ROLE_DISPLAY_NODE,
        )

        console.draw(
            surface,
            config=config,
            peers=[peer],
            selected_peer=peer,
            fps=60.0,
            fish_count=12,
            paused=False,
            effective_role=ROLE_ADMIN,
            admin_id="node-a",
        )

        labels = {button.label for button in console.buttons}
        self.assertIn("暂停", labels)
        self.assertIn("网络 开", labels)
        self.assertIn("音效 开", labels)
        self.assertIn("左", labels)
        self.assertIn("自动拓扑 开", labels)

        self.assertTrue(any("演" in label for label in labels))

        pause_button = next(button for button in console.buttons if button.label == "暂停")
        action = console.handle_click(pause_button.rect.center)
        self.assertIsNotNone(action)
        self.assertEqual(action.name, "toggle_pause")

        auto_button = next(button for button in console.buttons if button.label == "自动拓扑 开")
        auto_action = console.handle_click(auto_button.rect.center)
        self.assertEqual(auto_action.name, "toggle_auto_topology")

    def test_display_node_draws_status_without_controls(self) -> None:
        console = ControlConsole()
        surface = pygame.Surface((900, 700))
        config = AppConfig(node_id="node-display")
        peer = Peer(
            "node-admin",
            "admin",
            "127.0.0.1",
            37777,
            (800, 600),
            0.0,
            role=ROLE_ADMIN,
        )

        console.draw(
            surface,
            config=config,
            peers=[peer],
            selected_peer=None,
            fps=60.0,
            fish_count=12,
            paused=False,
            effective_role=ROLE_DISPLAY_NODE,
            admin_id="node-admin",
        )

        self.assertEqual(console.buttons, [])

    def test_run_status_text_prioritizes_offline_before_pause_state(self) -> None:
        config = AppConfig(node_id="node-a")
        peer = self.make_peer()

        config.network_enabled = False
        self.assertEqual(ControlConsole._run_status_text(config, [peer], paused=False), "未联机")

        config.network_enabled = True
        self.assertEqual(ControlConsole._run_status_text(config, [], paused=False), "未联机")
        self.assertEqual(ControlConsole._run_status_text(config, [peer], paused=True), "暂停")
        self.assertEqual(ControlConsole._run_status_text(config, [peer], paused=False), "运行中")

    def test_admin_and_display_panels_draw_explicit_run_status(self) -> None:
        console = ControlConsole()
        surface = pygame.Surface((900, 700))
        texts: list[str] = []
        original_draw_text = console._draw_text

        def capture_text(
            surface: pygame.Surface,
            text: str,
            position: tuple[int, int],
            font: pygame.font.Font,
            color: tuple[int, int, int],
        ) -> None:
            texts.append(text)
            original_draw_text(surface, text, position, font, color)

        console._draw_text = capture_text  # type: ignore[method-assign]
        admin_config = AppConfig(node_id="node-a", role=ROLE_ADMIN, admin_id="node-a")
        display_peer = self.make_peer()
        console.draw(
            surface,
            config=admin_config,
            peers=[display_peer],
            selected_peer=display_peer,
            fps=60.0,
            fish_count=12,
            paused=False,
            effective_role=ROLE_ADMIN,
            admin_id="node-a",
        )
        self.assertTrue(any(text.startswith("运行状态 运行中") for text in texts))

        texts.clear()
        display_config = AppConfig(node_id="node-display", role=ROLE_DISPLAY_NODE, admin_id="node-admin")
        admin_peer = self.make_peer("node-admin", ROLE_ADMIN)
        console.draw(
            surface,
            config=display_config,
            peers=[admin_peer],
            selected_peer=None,
            fps=60.0,
            fish_count=12,
            paused=False,
            effective_role=ROLE_DISPLAY_NODE,
            admin_id="node-admin",
        )
        self.assertTrue(any("运行状态 运行中" in text for text in texts))

    def test_topology_text_shows_placeholder_and_online_status(self) -> None:
        from cyberfish.config import AppConfig
        from cyberfish.network import Peer

        config = AppConfig(node_id="node-a")
        config.topology["left"] = "node-online"
        config.topology["right"] = "node-offline"
        peers = [Peer("node-online", "host", "127.0.0.1", 37777, (800, 600), 0.0)]
        text = ControlConsole._topology_text(config, peers)
        self.assertIn("无邻居", text)  # up/down 为空
        self.assertIn("在线", text)
        self.assertIn("离线", text)


if __name__ == "__main__":
    unittest.main()
