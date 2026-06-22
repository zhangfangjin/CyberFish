from __future__ import annotations

from datetime import datetime, timedelta
import os
import unittest
from unittest.mock import patch

from cyberfish.config import AppConfig
from cyberfish.storage import ConfigSnapshot, MetricReport, MySQLSettings
from cyberfish.storage.metrics import MetricAggregator


class ConfigSnapshotTests(unittest.TestCase):
    def test_snapshot_round_trip_and_normalization(self) -> None:
        config = AppConfig(
            node_id="node-a",
            fish_count=999,
            speed_multiplier=9.0,
            managed_config_version=7,
        )
        snapshot = ConfigSnapshot.from_config(config)
        restored = ConfigSnapshot.from_dict(snapshot.to_dict())

        self.assertEqual(restored.version, 7)
        self.assertEqual(restored.fish_count, 200)
        self.assertEqual(restored.speed_multiplier, 4.0)
        self.assertEqual(restored.node.window_width, 1280)

    def test_manual_topology_is_included_only_with_database_version(self) -> None:
        config = AppConfig(node_id="node-a", auto_topology=False)
        config.topology["right"] = "node-b"
        self.assertIsNone(ConfigSnapshot.from_config(config).topology["right"])

        config.manual_topology_version = "topology-1"
        snapshot = ConfigSnapshot.from_config(config)
        self.assertEqual(snapshot.topology["right"], "node-b")
        self.assertEqual(snapshot.topologies["node-a"]["right"], "node-b")


class MetricAggregatorTests(unittest.TestCase):
    def report(
        self,
        sequence: int,
        transfer_sent: int,
        *,
        boot_id: str = "boot-a",
        fish_count: int = 3,
    ) -> MetricReport:
        return MetricReport(
            node_id="node-a",
            boot_id=boot_id,
            sequence=sequence,
            fish_count=fish_count,
            fps=59.5,
            counters={"transfer_sent": transfer_sent},
        )

    def test_cumulative_reports_become_one_minute_delta_row(self) -> None:
        aggregator = MetricAggregator("run-a")
        start = datetime(2026, 6, 22, 12, 0, 5)

        self.assertEqual(aggregator.add(self.report(1, 10), start), [])
        self.assertEqual(
            aggregator.add(self.report(1, 10), start + timedelta(seconds=2)),
            [],
        )
        self.assertEqual(
            aggregator.add(self.report(2, 14, fish_count=5), start + timedelta(seconds=10)),
            [],
        )
        flushed = aggregator.add(self.report(3, 15), start + timedelta(minutes=1))

        self.assertEqual(len(flushed), 1)
        metric = flushed[0]
        self.assertEqual(metric.sample_count, 2)
        self.assertEqual(metric.fish_count_sum, 8)
        self.assertEqual(metric.transfer_sent, 4)
        self.assertEqual(metric.online_seconds, 10)

    def test_new_boot_establishes_new_counter_baseline(self) -> None:
        aggregator = MetricAggregator("run-a")
        start = datetime(2026, 6, 22, 12, 0, 0)
        aggregator.add(self.report(1, 100), start)
        aggregator.add(self.report(1, 2, boot_id="boot-b"), start + timedelta(seconds=10))

        metric = aggregator.flush_all()[0]
        self.assertEqual(metric.transfer_sent, 0)


class MySQLSettingsTests(unittest.TestCase):
    def test_database_is_opt_in_and_password_is_read_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CYBERFISH_DB_ENABLED": "1",
                "CYBERFISH_DB_HOST": "db.local",
                "CYBERFISH_DB_PASSWORD": "secret",
            },
            clear=True,
        ):
            settings = MySQLSettings.from_env()

        self.assertTrue(settings.enabled)
        self.assertEqual(settings.host, "db.local")
        self.assertEqual(settings.password, "secret")


if __name__ == "__main__":
    unittest.main()
