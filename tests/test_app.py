from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pygame

from cyberfish.app import CyberFishApp
from cyberfish.config import load_config
from cyberfish.controls import ControlAction
from cyberfish.network import Peer


class AppDisplayTests(unittest.TestCase):
    def test_display_mode_falls_back_to_primary_display_on_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = CyberFishApp(Path(temp_dir) / "config.json", force_network_enabled=False)
            app.config.display_index = 2
            fake_surface = object()

            with patch("pygame.display.set_mode") as set_mode:
                set_mode.side_effect = [pygame.error("display unavailable"), fake_surface]

                screen = app._set_display_mode((800, 600), fullscreen=False)

                self.assertIs(screen, fake_surface)
                self.assertEqual(set_mode.call_args_list[0].args[:2], ((800, 600), pygame.RESIZABLE))
                self.assertEqual(set_mode.call_args_list[0].kwargs.get("display"), 2)
                self.assertEqual(set_mode.call_args_list[1].args[:2], ((800, 600), pygame.RESIZABLE))
                self.assertEqual(set_mode.call_args_list[1].kwargs.get("display"), 0)

    def test_fullscreen_uses_plain_fullscreen_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = CyberFishApp(Path(temp_dir) / "config.json", force_network_enabled=False)
            fake_surface = object()

            with patch("pygame.display.set_mode") as set_mode:
                set_mode.return_value = fake_surface

                screen = app._set_display_mode((1024, 768), fullscreen=True)

            self.assertIs(screen, fake_surface)
            self.assertEqual(set_mode.call_args.args[:2], ((1024, 768), pygame.FULLSCREEN))

    def test_console_actions_update_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = CyberFishApp(Path(temp_dir) / "config.json", force_network_enabled=False)
            app.config.fish_count = 1
            app.fishes = []

            app._handle_console_action(ControlAction("toggle_pause"))
            self.assertTrue(app.paused)

            app._handle_console_action(ControlAction("fish_inc"))
            self.assertEqual(app.config.fish_count, 2)
            self.assertEqual(len(app.fishes), 2)

            app._handle_console_action(ControlAction("speed_inc"))
            self.assertEqual(app.config.speed_multiplier, 1.1)

    def test_mouse_click_uses_scaled_display_coordinates(self) -> None:
        class FakeScreen:
            def get_size(self) -> tuple[int, int]:
                return (800, 600)

        class FakeRenderer:
            def __init__(self) -> None:
                self.positions: list[tuple[int, int]] = []

            def handle_console_click(self, position: tuple[int, int]) -> ControlAction | None:
                self.positions.append(position)
                if position == (100, 100):
                    return ControlAction("toggle_pause")
                return None

        with tempfile.TemporaryDirectory() as temp_dir:
            app = CyberFishApp(Path(temp_dir) / "config.json", force_network_enabled=False)
            renderer = FakeRenderer()
            app.screen = FakeScreen()  # type: ignore[assignment]
            app.renderer = renderer  # type: ignore[assignment]

            with patch("pygame.display.get_window_size", return_value=(1600, 1200)):
                app._handle_mouse_click((200, 200))

            self.assertTrue(app.paused)
            self.assertIn((200, 200), renderer.positions)
            self.assertIn((100, 100), renderer.positions)

    def test_mouse_click_falls_back_to_retina_physical_pixels(self) -> None:
        class FakeScreen:
            def get_size(self) -> tuple[int, int]:
                return (800, 600)

        class FakeRenderer:
            def __init__(self) -> None:
                self.positions: list[tuple[int, int]] = []

            def handle_console_click(self, position: tuple[int, int]) -> ControlAction | None:
                self.positions.append(position)
                if position == (200, 200):
                    return ControlAction("toggle_pause")
                return None

        with tempfile.TemporaryDirectory() as temp_dir:
            app = CyberFishApp(Path(temp_dir) / "config.json", force_network_enabled=False)
            renderer = FakeRenderer()
            app.screen = FakeScreen()  # type: ignore[assignment]
            app.renderer = renderer  # type: ignore[assignment]

            with patch("pygame.display.get_window_size", return_value=(800, 600)):
                app._handle_mouse_click((400, 400))

            self.assertTrue(app.paused)
            self.assertIn((200, 200), renderer.positions)

    def test_console_assigns_selected_peer_to_one_direction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = CyberFishApp(Path(temp_dir) / "config.json", force_network_enabled=False)
            peers = [
                Peer("node-b", "host-b", "127.0.0.1", 37777, (800, 600), 0.0),
                Peer("node-c", "host-c", "127.0.0.1", 37778, (800, 600), 0.0),
            ]
            app._peers = lambda: peers  # type: ignore[method-assign]

            app._handle_console_action(ControlAction("select_peer", 1))
            app._handle_console_action(ControlAction("assign_direction", "right"))
            app._handle_console_action(ControlAction("assign_direction", "up"))

            self.assertEqual(app.config.topology["up"], "node-c")
            self.assertIsNone(app.config.topology["right"])

    def test_toggle_auto_topology_updates_config_and_coordinator(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = CyberFishApp(Path(temp_dir) / "config.json", force_network_enabled=False)
            self.assertTrue(app.config.auto_topology)
            self.assertTrue(app.topology.auto_mode)

            app._handle_console_action(ControlAction("toggle_auto_topology"))
            self.assertFalse(app.config.auto_topology)
            self.assertFalse(app.topology.auto_mode)

            # 持久化后重载应保留关闭状态。
            reloaded = CyberFishApp(Path(temp_dir) / "config.json", force_network_enabled=False)
            self.assertFalse(reloaded.config.auto_topology)

    def test_forced_network_off_does_not_persist_when_other_settings_are_saved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            app = CyberFishApp(path, force_network_enabled=False)

            app._handle_console_action(ControlAction("speed_inc"))

            self.assertTrue(load_config(path).network_enabled)

    def test_network_start_failure_is_runtime_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            app = CyberFishApp(path)

            with patch("cyberfish.app.NetworkManager", side_effect=OSError("port busy")):
                app._start_network()

            self.assertIsNone(app.network)
            self.assertTrue(app.config.network_enabled)
            app._shutdown()
            self.assertTrue(load_config(path).network_enabled)

    def test_manual_assign_rejects_unknown_peer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = CyberFishApp(Path(temp_dir) / "config.json", force_network_enabled=False)
            app._peers = lambda: []  # type: ignore[method-assign]
            # 无在线主机时选择为空，分配应无副作用。
            app._handle_console_action(ControlAction("assign_direction", "left"))
            self.assertIsNone(app.config.topology["left"])


if __name__ == "__main__":
    unittest.main()
