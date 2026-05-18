from __future__ import annotations

import unittest

from cyberfish.network import NetworkManager, Peer, TRANSFER_TIMEOUT_SECONDS


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class NetworkTests(unittest.TestCase):
    def make_manager(self, *args, **kwargs) -> NetworkManager:
        try:
            return NetworkManager(*args, **kwargs)
        except PermissionError as exc:
            self.skipTest(f"UDP sockets are blocked in this sandbox: {exc}")

    def test_loopback_discovery_transfer_and_ack(self) -> None:
        clock = FakeClock()
        a = self.make_manager("node-a", 0, broadcast_port=0, hostname="a", now_func=clock)
        b = self.make_manager("node-b", 0, broadcast_port=0, hostname="b", now_func=clock)
        try:
            a.send_hello_to(("127.0.0.1", b.listen_port))
            events_b = b.poll()
            self.assertEqual(events_b.discovered[0].node_id, "node-a")

            b.send_hello_to(("127.0.0.1", a.listen_port))
            events_a = a.poll()
            self.assertEqual(events_a.discovered[0].node_id, "node-b")

            payload = {
                "fish_id": "fish-1",
                "direction": "right",
                "edge_position": 0.5,
                "velocity": [120, 0],
                "size": 50,
                "color": [255, 120, 90],
                "depth": 0.5,
                "phase": 0.0,
            }
            peer_b = a.get_peer("node-b")
            self.assertIsNotNone(peer_b)
            transfer_id = a.send_fish_transfer(peer_b, payload)  # type: ignore[arg-type]
            events_b = b.poll()
            self.assertEqual(len(events_b.transfers), 1)
            self.assertEqual(events_b.transfers[0]["fish_id"], "fish-1")
            events_a = a.poll()
            self.assertIn(transfer_id, events_a.acked_transfer_ids)
            self.assertNotIn(transfer_id, a.pending_transfers)
        finally:
            a.close()
            b.close()

    def test_unacked_transfer_expires_for_restore(self) -> None:
        clock = FakeClock()
        manager = self.make_manager("node-a", 0, broadcast_port=0, now_func=clock)
        try:
            peer = Peer(
                node_id="missing",
                hostname="missing",
                address="127.0.0.1",
                port=9,
                screen_size=(800, 600),
                last_seen=clock(),
            )
            payload = {
                "fish_id": "fish-2",
                "direction": "left",
                "edge_position": 0.5,
                "velocity": [-100, 0],
                "size": 50,
                "color": [1, 2, 3],
                "depth": 0.5,
                "phase": 0.0,
            }
            manager.send_fish_transfer(peer, payload)
            clock.advance(TRANSFER_TIMEOUT_SECONDS + 0.01)
            events = manager.poll()
            self.assertEqual(events.expired_transfers[0]["fish_id"], "fish-2")
            self.assertFalse(manager.pending_transfers)
        finally:
            manager.close()


if __name__ == "__main__":
    unittest.main()
