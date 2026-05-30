from __future__ import annotations

import time
import unittest

from cyberfish.network import (
    NetworkManager,
    Peer,
    TRANSFER_TIMEOUT_SECONDS,
    detect_local_ip,
    subnet_broadcast_for,
)


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


class BroadcastHelperTests(unittest.TestCase):
    def test_subnet_broadcast_for_valid_ip(self) -> None:
        self.assertEqual(subnet_broadcast_for("10.0.0.91"), "10.0.0.255")
        self.assertEqual(subnet_broadcast_for("192.168.191.209"), "192.168.191.255")

    def test_subnet_broadcast_rejects_loopback_and_garbage(self) -> None:
        self.assertIsNone(subnet_broadcast_for("127.0.0.1"))
        self.assertIsNone(subnet_broadcast_for(None))
        self.assertIsNone(subnet_broadcast_for("not-an-ip"))
        self.assertIsNone(subnet_broadcast_for("1.2.3"))

    def test_detect_local_ip_returns_string_or_none(self) -> None:
        ip = detect_local_ip()
        self.assertTrue(ip is None or isinstance(ip, str))


class NodeIdConflictTests(unittest.TestCase):
    def make_manager(self, *args, **kwargs) -> NetworkManager:
        try:
            return NetworkManager(*args, **kwargs)
        except PermissionError as exc:
            self.skipTest(f"UDP sockets are blocked in this sandbox: {exc}")

    def test_same_node_id_from_remote_flags_conflict(self) -> None:
        from cyberfish.network import NetworkEvents

        manager = self.make_manager("node-dup", 0, broadcast_port=0)
        try:
            manager.local_ip = "10.0.0.5"
            events = NetworkEvents()
            raw = b'{"type":"hello","node_id":"node-dup"}'
            # 来自其它主机 IP 的相同 node_id 报文应判定为冲突。
            manager._handle_datagram(raw, ("10.0.0.9", 5000), events)
            self.assertTrue(events.node_id_conflict)
        finally:
            manager.close()

    def test_same_node_id_from_self_ip_is_not_conflict(self) -> None:
        from cyberfish.network import NetworkEvents

        manager = self.make_manager("node-dup", 0, broadcast_port=0)
        try:
            manager.local_ip = "10.0.0.5"
            events = NetworkEvents()
            raw = b'{"type":"hello","node_id":"node-dup"}'
            # 本机回环/自身 IP 的报文是自己的广播，不算冲突。
            manager._handle_datagram(raw, ("10.0.0.5", 5000), events)
            manager._handle_datagram(raw, ("127.0.0.1", 5000), events)
            self.assertFalse(events.node_id_conflict)
        finally:
            manager.close()


class ReverseDiscoveryTests(unittest.TestCase):
    def make_manager(self, *args, **kwargs) -> NetworkManager:
        try:
            return NetworkManager(*args, **kwargs)
        except PermissionError as exc:
            self.skipTest(f"UDP sockets are blocked in this sandbox: {exc}")

    def test_unicast_hello_completes_discovery_when_broadcast_one_way(self) -> None:
        clock = FakeClock()
        a = self.make_manager("node-a", 0, broadcast_port=0, hostname="mac", now_func=clock)
        b = self.make_manager("node-b", 0, broadcast_port=0, hostname="win", now_func=clock)
        try:
            # 模拟 B 的广播完全不可达（多网卡只从虚拟网卡发出）。
            b.broadcast_targets = []
            # A 的广播能到达 B（这里用定向 hello 模拟）。
            a.send_hello_to(("127.0.0.1", b.listen_port))

            ok = False
            for _ in range(40):
                b.poll()  # B 发现 A 后单播 hello 回 A
                a.poll()  # A 通过单播 hello 发现 B
                if a.get_peer("node-b") and b.get_peer("node-a"):
                    ok = True
                    break
                time.sleep(0.02)
            self.assertTrue(ok, "单向广播下应通过单播回复补全双向发现")
        finally:
            a.close()
            b.close()


if __name__ == "__main__":
    unittest.main()