from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pygame

from cyberfish.app import CyberFishApp
from cyberfish.config import ROLE_ADMIN, ROLE_DISPLAY_NODE, load_config
from cyberfish.controls import ControlAction
from cyberfish.fish import Fish
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
            app = CyberFishApp(
                Path(temp_dir) / "config.json",
                force_network_enabled=False,
                role_override=ROLE_ADMIN,
            )
            app.config.fish_count = 1
            app.fishes = []

            app._handle_console_action(ControlAction("toggle_pause"))
            self.assertTrue(app.paused)

            app._handle_console_action(ControlAction("fish_inc"))
            self.assertEqual(app.config.fish_count, 2)
            self.assertEqual(len(app.fishes), 2)

            app._handle_console_action(ControlAction("speed_inc"))
            self.assertEqual(app.config.speed_multiplier, 1.1)

    def test_display_node_ignores_local_console_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = CyberFishApp(Path(temp_dir) / "config.json", force_network_enabled=False)
            app._handle_console_action(ControlAction("toggle_pause"))
            self.assertFalse(app.paused)

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
            app = CyberFishApp(
                Path(temp_dir) / "config.json",
                force_network_enabled=False,
                role_override=ROLE_ADMIN,
            )
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
            app = CyberFishApp(
                Path(temp_dir) / "config.json",
                force_network_enabled=False,
                role_override=ROLE_ADMIN,
            )
            renderer = FakeRenderer()
            app.screen = FakeScreen()  # type: ignore[assignment]
            app.renderer = renderer  # type: ignore[assignment]

            with patch("pygame.display.get_window_size", return_value=(800, 600)):
                app._handle_mouse_click((400, 400))

            self.assertTrue(app.paused)
            self.assertIn((200, 200), renderer.positions)

    def test_console_assigns_selected_peer_to_one_direction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = CyberFishApp(
                Path(temp_dir) / "config.json",
                force_network_enabled=False,
                role_override=ROLE_ADMIN,
            )
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

    def test_manual_topology_assignment_is_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            app = CyberFishApp(path, force_network_enabled=False, role_override=ROLE_ADMIN)
            peers = [Peer("node-b", "host-b", "127.0.0.1", 37777, (800, 600), 0.0)]
            app._peers = lambda: peers  # type: ignore[method-assign]

            app._handle_console_action(ControlAction("select_peer", 0))
            app._handle_console_action(ControlAction("assign_direction", "right"))

            self.assertEqual(app.config.topology["right"], "node-b")
            reloaded = CyberFishApp(path, force_network_enabled=False)
            self.assertIsNone(reloaded.config.topology["right"])

    def test_toggle_auto_topology_updates_config_and_coordinator(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = CyberFishApp(
                Path(temp_dir) / "config.json",
                force_network_enabled=False,
                role_override=ROLE_ADMIN,
            )
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
            app = CyberFishApp(path, force_network_enabled=False, role_override=ROLE_ADMIN)

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
            app = CyberFishApp(
                Path(temp_dir) / "config.json",
                force_network_enabled=False,
                role_override=ROLE_ADMIN,
            )
            app._peers = lambda: []  # type: ignore[method-assign]
            # 无在线主机时选择为空，分配应无副作用。
            app._handle_console_action(ControlAction("assign_direction", "left"))
            self.assertIsNone(app.config.topology["left"])

    def test_admin_override_is_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            app = CyberFishApp(path, force_network_enabled=False, role_override=ROLE_ADMIN)
            app._handle_console_action(ControlAction("speed_inc"))

            self.assertEqual(app.config.role, ROLE_ADMIN)
            self.assertEqual(load_config(path).role, ROLE_DISPLAY_NODE)

    def test_admin_conflict_demotes_larger_node_id(self) -> None:
        class FakeNetwork:
            def __init__(self) -> None:
                self.role = None

            def sorted_peers(self) -> list[Peer]:
                return [
                    Peer(
                        "node-a",
                        "admin-a",
                        "127.0.0.1",
                        37777,
                        (800, 600),
                        0.0,
                        role=ROLE_ADMIN,
                    )
                ]

            def set_role(self, role: str) -> None:
                self.role = role

        with tempfile.TemporaryDirectory() as temp_dir:
            app = CyberFishApp(
                Path(temp_dir) / "config.json",
                force_network_enabled=False,
                role_override=ROLE_ADMIN,
            )
            app.config.node_id = "node-b"
            app.network = FakeNetwork()  # type: ignore[assignment]

            app._update_role_state()

            self.assertEqual(app.config.admin_id, "node-a")
            self.assertEqual(app.effective_role, ROLE_DISPLAY_NODE)
            self.assertTrue(app.admin_conflict)
            self.assertEqual(app.network.role, ROLE_DISPLAY_NODE)  # type: ignore[union-attr]

    def test_display_node_executes_only_current_admin_command(self) -> None:
        class FakeNetwork:
            def __init__(self, peers: dict[str, Peer]) -> None:
                self.peers = peers

            def get_peer(self, node_id: str | None) -> Peer | None:
                return self.peers.get(node_id or "")

        with tempfile.TemporaryDirectory() as temp_dir:
            app = CyberFishApp(Path(temp_dir) / "config.json", force_network_enabled=False)
            app.config.node_id = "node-display"
            app.config.admin_id = "node-admin"
            app.network = FakeNetwork(
                {
                    "node-admin": Peer(
                        "node-admin",
                        "admin",
                        "127.0.0.1",
                        37777,
                        (800, 600),
                        0.0,
                        role=ROLE_ADMIN,
                    ),
                    "node-ordinary": Peer(
                        "node-ordinary",
                        "ordinary",
                        "127.0.0.1",
                        37778,
                        (800, 600),
                        0.0,
                    ),
                }
            )  # type: ignore[assignment]

            ok, _ = app._execute_admin_command(
                {
                    "node_id": "node-ordinary",
                    "admin_id": "node-ordinary",
                    "action": "pause",
                    "payload": {},
                }
            )
            self.assertFalse(ok)
            self.assertFalse(app.paused)

            ok, _ = app._execute_admin_command(
                {
                    "node_id": "node-admin",
                    "admin_id": "node-admin",
                    "action": "pause",
                    "payload": {},
                }
            )
            self.assertTrue(ok)
            self.assertTrue(app.paused)


class AppFishStateSyncTests(unittest.TestCase):
    class FakeScreen:
        def __init__(self, size: tuple[int, int] = (800, 600)) -> None:
            self.size = size

        def get_size(self) -> tuple[int, int]:
            return self.size

    class FakeNetwork:
        def __init__(self, peers: dict[str, Peer] | None = None) -> None:
            self.peers = peers or {}
            self.sent_states: list[tuple[int, list[dict]]] = []
            self.screen_size: tuple[int, int] | None = None

        def get_peer(self, node_id: str | None) -> Peer | None:
            return self.peers.get(node_id or "")

        def sorted_peers(self) -> list[Peer]:
            return list(self.peers.values())

        def update_screen_size(self, screen_size: tuple[int, int]) -> None:
            self.screen_size = screen_size

        def send_fish_state(self, fish_count: int, fishes: list[dict]) -> int:
            self.sent_states.append((fish_count, fishes))
            return len(self.sent_states)

    def make_fish(self, fish_id: str, x: float, y: float) -> Fish:
        return Fish(
            fish_id=fish_id,
            position=pygame.Vector2(x, y),
            velocity=pygame.Vector2(120, 0),
            size=50,
            color=(1, 2, 3),
            depth=0.7,
            phase=1.0,
            wander_angle=0.0,
            turn_progress=0.2,
            turn_duration=0.55,
            turn_direction=1,
        )

    def make_app(self) -> CyberFishApp:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        app = CyberFishApp(
            Path(self.temp_dir.name) / "config.json",
            force_network_enabled=False,
        )
        app.config.node_id = "node-self"
        app.screen = self.FakeScreen()  # type: ignore[assignment]
        return app

    def test_state_sync_tick_sends_all_local_fishes(self) -> None:
        app = self.make_app()
        network = self.FakeNetwork()
        app.network = network  # type: ignore[assignment]
        app.fishes = [self.make_fish(f"fish-{index}", index * 10, 300) for index in range(12)]

        app._state_sync_tick(1.0)

        self.assertEqual(network.screen_size, (800, 600))
        self.assertEqual(len(network.sent_states), 1)
        fish_count, fishes = network.sent_states[0]
        self.assertEqual(fish_count, 12)
        self.assertEqual(len(fishes), 12)
        self.assertEqual(fishes[0]["currentNodeId"], "node-self")
        self.assertEqual(app.fishes[0].current_node_id, "node-self")

    def test_peer_snapshots_drop_old_sequences_and_render_only_adjacent_edge_ghosts(self) -> None:
        app = self.make_app()
        app.network = self.FakeNetwork(
            {
                "node-right": Peer("node-right", "right", "127.0.0.1", 37777, (1000, 600), 10.0),
                "node-other": Peer("node-other", "other", "127.0.0.1", 37778, (1000, 600), 10.0),
            }
        )  # type: ignore[assignment]
        app.config.topology["right"] = "node-right"

        edge_fish = self.make_fish("edge", -20, 300)
        app._handle_peer_fish_state(
            {
                "node_id": "node-right",
                "sequence": 2,
                "screen_size": [1000, 600],
                "fish_count": 1,
                "fishes": [edge_fish.to_state_payload((1000, 600))],
            },
            10.0,
        )
        app._handle_peer_fish_state(
            {
                "node_id": "node-right",
                "sequence": 1,
                "screen_size": [1000, 600],
                "fish_count": 1,
                "fishes": [self.make_fish("old", -20, 300).to_state_payload((1000, 600))],
            },
            10.1,
        )
        app._handle_peer_fish_state(
            {
                "node_id": "node-other",
                "sequence": 1,
                "screen_size": [1000, 600],
                "fish_count": 1,
                "fishes": [self.make_fish("non-adjacent", -20, 300).to_state_payload((1000, 600))],
            },
            10.1,
        )

        self.assertEqual(app.peer_fish_states["node-right"].sequence, 2)
        ghosts = app._adjacent_edge_ghosts(10.2)

        self.assertEqual([fish.fish_id for fish in ghosts], ["edge"])
        self.assertAlmostEqual(ghosts[0].position.x, 784, delta=1.0)
        self.assertAlmostEqual(ghosts[0].position.y, 300, delta=1.0)

        app.fishes = [self.make_fish("edge", 790, 300)]
        self.assertEqual(app._adjacent_edge_ghosts(10.2), [])

        app.fishes = []
        app.config.topology["right"] = None
        self.assertEqual(app._adjacent_edge_ghosts(10.2), [])

    def test_peer_topology_snapshot_fields_follow_claims_and_local_fallback(self) -> None:
        app = self.make_app()
        right_peer = Peer("node-right", "right", "127.0.0.1", 37777, (1000, 600), 10.0)
        far_peer = Peer("node-far", "far", "127.0.0.1", 37778, (1000, 600), 10.0)
        up_peer = Peer("node-up", "up", "127.0.0.1", 37779, (1000, 600), 10.0)
        app.network = self.FakeNetwork(
            {
                "node-right": right_peer,
                "node-far": far_peer,
                "node-up": up_peer,
            }
        )  # type: ignore[assignment]
        app.config.topology["right"] = "node-right"
        app.config.topology["up"] = "node-up"
        app.topology.on_claim(
            {
                "type": "topology",
                "node_id": "node-right",
                "topology": {
                    "left": "node-self",
                    "right": "node-far",
                    "up": None,
                    "down": None,
                },
            }
        )
        app.topology.on_claim(
            {
                "type": "topology",
                "node_id": "node-far",
                "topology": {
                    "left": "node-right",
                    "right": None,
                    "up": None,
                    "down": None,
                },
            }
        )

        app._sync_peer_topology_snapshots()

        self.assertEqual((right_peer.position_x, right_peer.position_y), (1, 0))
        self.assertEqual(right_peer.left_neighbor, "node-self")
        self.assertEqual(right_peer.right_neighbor, "node-far")
        self.assertTrue(right_peer.online_status)
        self.assertEqual((far_peer.position_x, far_peer.position_y), (2, 0))
        self.assertEqual(far_peer.left_neighbor, "node-right")
        self.assertEqual((up_peer.position_x, up_peer.position_y), (0, -1))
        self.assertEqual(up_peer.down_neighbor, "node-self")

    def test_peer_snapshot_cache_expires(self) -> None:
        app = self.make_app()
        app.network = self.FakeNetwork(
            {"node-right": Peer("node-right", "right", "127.0.0.1", 37777, (1000, 600), 10.0)}
        )  # type: ignore[assignment]
        app._handle_peer_fish_state(
            {
                "node_id": "node-right",
                "sequence": 1,
                "screen_size": [1000, 600],
                "fish_count": 1,
                "fishes": [self.make_fish("edge", -20, 300).to_state_payload((1000, 600))],
            },
            10.0,
        )

        app._drop_stale_peer_fish_states(10.7)

        self.assertNotIn("node-right", app.peer_fish_states)


if __name__ == "__main__":
    unittest.main()
