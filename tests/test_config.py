from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cyberfish.config import DIRECTIONS, AppConfig, load_config, save_config


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


if __name__ == "__main__":
    unittest.main()
