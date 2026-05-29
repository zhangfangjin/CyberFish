from __future__ import annotations

import unittest

import pygame

from cyberfish.config import AppConfig
from cyberfish.controls import ControlConsole
from cyberfish.network import Peer


class ControlConsoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pygame.font.init()

    def test_draw_creates_clickable_chinese_controls(self) -> None:
        console = ControlConsole()
        surface = pygame.Surface((900, 700))
        config = AppConfig(node_id="node-a")
        peer = Peer("node-b", "host-b", "127.0.0.1", 37777, (800, 600), 0.0)

        console.draw(
            surface,
            config=config,
            peers=[peer],
            selected_peer=peer,
            fps=60.0,
            fish_count=12,
            paused=False,
        )

        labels = {button.label for button in console.buttons}
        self.assertIn("暂停", labels)
        self.assertIn("网络 开", labels)
        self.assertIn("音效 开", labels)
        self.assertIn("左", labels)
        self.assertIn("自动拓扑 开", labels)

        pause_button = next(button for button in console.buttons if button.label == "暂停")
        action = console.handle_click(pause_button.rect.center)
        self.assertIsNotNone(action)
        self.assertEqual(action.name, "toggle_pause")

        auto_button = next(button for button in console.buttons if button.label == "自动拓扑 开")
        auto_action = console.handle_click(auto_button.rect.center)
        self.assertEqual(auto_action.name, "toggle_auto_topology")

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
