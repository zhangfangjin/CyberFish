from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cyberfish.config import (
    DIRECTIONS,
    AppConfig,
    assign_peer_to_single_direction,
    load_config,
    save_config,
    sanitize_topology,
    topology_equal,
)


class ConfigTests(unittest.TestCase):
    def test_default_config_is_created_and_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            config = load_config(path)
            self.assertTrue(path.exists())
            self.assertEqual(set(config.topology), set(DIRECTIONS))
            self.assertGreaterEqual(config.fish_count, 1)

    def test_save_and_reload_topology(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            config = AppConfig(node_id="node-a")
            config.topology["right"] = "node-b"
            save_config(path, config)
            loaded = load_config(path)
            self.assertEqual(loaded.node_id, "node-a")
            self.assertEqual(loaded.topology["right"], "node-b")

    def test_peer_can_only_be_assigned_to_one_direction(self) -> None:
        config = AppConfig(node_id="node-a")
        assign_peer_to_single_direction(config.topology, "node-b", "right")
        assign_peer_to_single_direction(config.topology, "node-b", "up")

        self.assertIsNone(config.topology["right"])
        self.assertEqual(config.topology["up"], "node-b")
        self.assertEqual(
            sum(1 for peer_id in config.topology.values() if peer_id == "node-b"),
            1,
        )

    def test_auto_topology_defaults_true_and_invalid_falls_back(self) -> None:
        config = AppConfig(node_id="node-a")
        self.assertTrue(config.auto_topology)
        # 非法值（非 bool）应在 normalized 中回退为 True（Requirement 1.2）。
        config.auto_topology = "yes"  # type: ignore[assignment]
        config.normalized()
        self.assertTrue(config.auto_topology)

    def test_missing_auto_topology_initialized_to_true(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text('{"node_id": "node-a"}', encoding="utf-8")
            config = load_config(path)
            self.assertTrue(config.auto_topology)
            # 写回后字段存在。
            reloaded = load_config(path)
            self.assertTrue(reloaded.auto_topology)

    def test_invalid_topology_structure_falls_back_to_nulls(self) -> None:
        sanitized = sanitize_topology({"left": 123, "right": "", "weird": "x"})
        self.assertEqual(set(sanitized), set(DIRECTIONS))
        self.assertTrue(all(value is None for value in sanitized.values()))

    def test_topology_equal_ignores_key_order(self) -> None:
        a = {"left": "n1", "right": None, "up": None, "down": "n2"}
        b = {"down": "n2", "up": None, "right": None, "left": "n1"}
        self.assertTrue(topology_equal(a, b))
        self.assertFalse(topology_equal(a, {**a, "left": "other"}))

    def test_normalized_keeps_topology_reference(self) -> None:
        config = AppConfig(node_id="node-a")
        topology_ref = config.topology
        config.normalized()
        # 原地更新，外部引用保持有效（协调器依赖此行为）。
        self.assertIs(config.topology, topology_ref)

    def test_save_config_returns_true_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            self.assertTrue(save_config(path, AppConfig(node_id="node-a")))

    def test_save_config_returns_false_and_preserves_file_on_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            config = AppConfig(node_id="node-a")
            config.topology["left"] = "node-orig"
            save_config(path, config)
            original = path.read_text(encoding="utf-8")

            # 把父目录变成不可写以触发写入失败。
            from unittest.mock import patch

            with patch("cyberfish.config.os.replace", side_effect=OSError("boom")):
                config.topology["left"] = "node-new"
                ok = save_config(path, config)
            self.assertFalse(ok)
            # 原文件未被破坏。
            self.assertEqual(path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
