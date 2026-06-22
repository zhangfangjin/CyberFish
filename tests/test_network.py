from __future__ import annotations

from collections import defaultdict
import json
import time
import unittest

from cyberfish.config import ROLE_ADMIN, ROLE_DISPLAY_NODE
from cyberfish.network import (
    DISCOVER,
    DISCOVER_RESPONSE,
    HEARTBEAT,
    CONFIG_ACK,
    CONFIG_SNAPSHOT,
    LEGACY_FISH_STATE,
    LEGACY_TOPOLOGY,
    NetworkEvents,
    NetworkManager,
    NODE_JOIN,
    NODE_LEAVE,
    NODE_METRICS,
    Peer,
    STATUS_SYNC,
    TOPOLOGY_UPDATE,
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


def make_fake_manager(node_id: str = "node-b", clock: FakeClock | None = None) -> NetworkManager:
    clock = clock or FakeClock()
    manager = NetworkManager.__new__(NetworkManager)
    manager.node_id = node_id
    manager.listen_port = 41000
    manager.hostname = f"host-{node_id}"
    manager.screen_size = (800, 600)
    manager.role = ROLE_DISPLAY_NODE
    manager.now = clock
    manager.peers = {}
    manager.pending_transfers = {}
    manager._received_transfers = {}
    manager._fish_state_sequence = 0
    manager.local_ip = "127.0.0.1"
    manager.broadcasted = []
    manager.sent_messages = []
    manager.stats = defaultdict(int)
    manager._broadcast = lambda message: manager.broadcasted.append(dict(message))
    manager._send_message = lambda message, address: manager.sent_messages.append((dict(message), address))
    return manager


class StandardProtocolTests(unittest.TestCase):
    def test_config_and_metric_management_messages(self) -> None:
        manager = make_fake_manager("node-a")
        manager.boot_id = "boot-a"
        manager.applied_config_version = 3
        peer = Peer("node-b", "host-b", "127.0.0.1", 42000, (800, 600), 0.0)

        manager.send_config_snapshot(peer, {"config_version": 3, "fish_count": 12})
        sequence = manager.send_node_metrics(12, 59.8)

        self.assertEqual(manager.sent_messages[0][0]["type"], CONFIG_SNAPSHOT)
        self.assertEqual(manager.sent_messages[0][0]["target_node_id"], "node-b")
        self.assertEqual(sequence, 1)
        self.assertEqual(manager.broadcasted[0]["type"], NODE_METRICS)
        self.assertEqual(manager.broadcasted[0]["boot_id"], "boot-a")
        self.assertEqual(manager.broadcasted[0]["applied_config_version"], 3)

    def test_inbound_config_snapshot_ack_and_metrics_create_events(self) -> None:
        manager = make_fake_manager("node-b")
        events = NetworkEvents()
        messages = [
            {
                "type": CONFIG_SNAPSHOT,
                "node_id": "node-a",
                "role": ROLE_ADMIN,
                "target_node_id": "node-b",
                "config": {"config_version": 2},
            },
            {
                "type": NODE_METRICS,
                "node_id": "node-a",
                "boot_id": "boot-a",
                "sequence": 1,
                "counters": {},
            },
        ]
        for message in messages:
            manager._handle_datagram(
                json.dumps(message).encode("utf-8"),
                ("127.0.0.1", 42000),
                events,
            )

        self.assertEqual(len(events.config_snapshots), 1)
        self.assertEqual(len(events.node_metrics), 1)

        admin = make_fake_manager("node-a")
        ack_events = NetworkEvents()
        ack = {
            "type": CONFIG_ACK,
            "node_id": "node-b",
            "target_node_id": "node-a",
            "config_version": 2,
            "ok": True,
        }
        admin._handle_datagram(
            json.dumps(ack).encode("utf-8"),
            ("127.0.0.1", 42000),
            ack_events,
        )
        self.assertEqual(len(ack_events.config_acks), 1)

    def test_outbound_methods_use_standard_message_types(self) -> None:
        manager = make_fake_manager("node-a")

        manager.send_node_join()
        manager.send_hello()
        manager.send_heartbeat()
        sequence = manager.send_fish_state(0, [])
        manager.send_topology_claim({"type": "topology", "topology": {"left": "node-b"}})
        manager.send_node_leave()

        self.assertEqual(sequence, 1)
        self.assertEqual(
            [message["type"] for message in manager.broadcasted],
            [NODE_JOIN, DISCOVER, HEARTBEAT, STATUS_SYNC, TOPOLOGY_UPDATE, NODE_LEAVE],
        )
        self.assertEqual(manager.broadcasted[3]["fish_count"], 0)
        self.assertEqual(manager.broadcasted[4]["topology"], {"left": "node-b"})

    def test_discover_registers_peer_and_sends_discover_response(self) -> None:
        manager = make_fake_manager("node-b")
        events = NetworkEvents()
        raw = json.dumps(
            {
                "type": DISCOVER,
                "node_id": "node-a",
                "role": ROLE_ADMIN,
                "hostname": "admin",
                "port": 42000,
                "screen_size": [1024, 768],
            }
        ).encode("utf-8")

        manager._handle_datagram(raw, ("127.0.0.1", 39999), events)

        peer = manager.get_peer("node-a")
        self.assertIsNotNone(peer)
        self.assertEqual(peer.hostname, "admin")  # type: ignore[union-attr]
        self.assertEqual(peer.port, 42000)  # type: ignore[union-attr]
        self.assertEqual(peer.screen_size, (1024, 768))  # type: ignore[union-attr]
        self.assertEqual(peer.role, ROLE_ADMIN)  # type: ignore[union-attr]
        self.assertEqual(events.discovered[0].node_id, "node-a")
        self.assertEqual(manager.sent_messages[0][0]["type"], DISCOVER_RESPONSE)
        self.assertEqual(manager.sent_messages[0][1], ("127.0.0.1", 42000))

    def test_discover_response_and_heartbeat_refresh_peer_without_reply(self) -> None:
        clock = FakeClock()
        manager = make_fake_manager("node-b", clock)
        events = NetworkEvents()
        response = json.dumps(
            {
                "type": DISCOVER_RESPONSE,
                "node_id": "node-a",
                "hostname": "peer",
                "port": 42000,
            }
        ).encode("utf-8")
        manager._handle_datagram(response, ("127.0.0.1", 42000), events)
        clock.advance(2.0)
        heartbeat = json.dumps(
            {
                "type": HEARTBEAT,
                "node_id": "node-a",
                "hostname": "peer",
                "port": 42000,
            }
        ).encode("utf-8")
        manager._handle_datagram(heartbeat, ("127.0.0.1", 42000), events)

        self.assertEqual(manager.get_peer("node-a").last_seen, clock())  # type: ignore[union-attr]
        self.assertEqual(manager.sent_messages, [])

    def test_node_join_and_leave_update_peer_membership(self) -> None:
        manager = make_fake_manager("node-b")
        events = NetworkEvents()
        join = json.dumps(
            {
                "type": NODE_JOIN,
                "node_id": "node-a",
                "hostname": "peer",
                "port": 42000,
            }
        ).encode("utf-8")
        leave = json.dumps({"type": NODE_LEAVE, "node_id": "node-a"}).encode("utf-8")

        manager._handle_datagram(join, ("127.0.0.1", 42000), events)
        self.assertIsNotNone(manager.get_peer("node-a"))

        manager._handle_datagram(leave, ("127.0.0.1", 42000), events)
        self.assertIsNone(manager.get_peer("node-a"))
        self.assertEqual(events.left_node_ids, ["node-a"])

    def test_standard_status_sync_and_legacy_fish_state_share_handler(self) -> None:
        manager = make_fake_manager("node-b")
        for message_type in (STATUS_SYNC, LEGACY_FISH_STATE):
            events = NetworkEvents()
            raw = json.dumps(
                {
                    "type": message_type,
                    "version": 2,
                    "node_id": f"node-{message_type}",
                    "screen_size": [640, 480],
                    "fish_count": 1,
                    "fishes": [{"id": "fish-1"}],
                }
            ).encode("utf-8")
            manager._handle_datagram(raw, ("127.0.0.1", 42000), events)

            self.assertEqual(len(events.fish_states), 1)
            self.assertEqual(events.fish_states[0]["screen_size"], [640, 480])

    def test_standard_and_legacy_topology_messages_create_claim_events(self) -> None:
        manager = make_fake_manager("node-b")
        events = NetworkEvents()
        for message_type in (TOPOLOGY_UPDATE, LEGACY_TOPOLOGY):
            raw = json.dumps(
                {
                    "type": message_type,
                    "node_id": f"node-{message_type}",
                    "topology": {"left": "node-b"},
                }
            ).encode("utf-8")
            manager._handle_datagram(raw, ("127.0.0.1", 42000), events)

        self.assertEqual([claim["type"] for claim in events.topology_claims], [TOPOLOGY_UPDATE, LEGACY_TOPOLOGY])
        self.assertIsNotNone(manager.get_peer(f"node-{TOPOLOGY_UPDATE}"))
        self.assertIsNotNone(manager.get_peer(f"node-{LEGACY_TOPOLOGY}"))


class NetworkTests(unittest.TestCase):
    def make_manager(self, *args, **kwargs) -> NetworkManager:
        try:
            return NetworkManager(*args, **kwargs)
        except PermissionError as exc:
            self.skipTest(f"UDP sockets are blocked in this sandbox: {exc}")

    def poll_until(
        self,
        manager: NetworkManager,
        predicate,
        *,
        attempts: int = 40,
    ) -> NetworkEvents:
        events = NetworkEvents()
        for _ in range(attempts):
            events = manager.poll()
            if predicate(events):
                return events
            time.sleep(0.01)
        return events

    def test_peer_defaults_include_requirement_node_fields(self) -> None:
        peer = Peer("node-b", "host-b", "127.0.0.1", 37777, (800, 600), 0.0)

        self.assertIsNone(peer.position_x)
        self.assertIsNone(peer.position_y)
        self.assertIsNone(peer.left_neighbor)
        self.assertIsNone(peer.right_neighbor)
        self.assertIsNone(peer.up_neighbor)
        self.assertIsNone(peer.down_neighbor)
        self.assertTrue(peer.online_status)

    def test_peer_refresh_preserves_topology_snapshot_fields(self) -> None:
        clock = FakeClock()
        manager = NetworkManager.__new__(NetworkManager)
        manager.node_id = "node-a"
        manager.peers = {
            "node-b": Peer(
                "node-b",
                "old-host",
                "127.0.0.1",
                37777,
                (800, 600),
                clock(),
                position_x=1,
                position_y=0,
                left_neighbor="node-a",
                right_neighbor="node-c",
                online_status=True,
            )
        }
        manager.now = clock

        events = NetworkEvents()
        manager._register_peer(
            "node-b",
            ("127.0.0.2", 38888),
            events,
            hostname="new-host",
            screen_size=(1024, 768),
        )

        peer = manager.get_peer("node-b")
        self.assertIsNotNone(peer)
        self.assertEqual(peer.hostname, "new-host")  # type: ignore[union-attr]
        self.assertEqual(peer.screen_size, (1024, 768))  # type: ignore[union-attr]
        self.assertEqual(peer.position_x, 1)  # type: ignore[union-attr]
        self.assertEqual(peer.position_y, 0)  # type: ignore[union-attr]
        self.assertEqual(peer.left_neighbor, "node-a")  # type: ignore[union-attr]
        self.assertEqual(peer.right_neighbor, "node-c")  # type: ignore[union-attr]
        self.assertTrue(peer.online_status)  # type: ignore[union-attr]

    def test_loopback_discovery_transfer_and_ack(self) -> None:
        clock = FakeClock()
        a = self.make_manager("node-a", 0, broadcast_port=0, hostname="a", now_func=clock)
        b = self.make_manager("node-b", 0, broadcast_port=0, hostname="b", now_func=clock)
        try:
            a.send_hello_to(("127.0.0.1", b.listen_port))
            self.poll_until(b, lambda events: b.get_peer("node-a") is not None)
            self.assertEqual(b.get_peer("node-a").node_id, "node-a")  # type: ignore[union-attr]

            b.send_hello_to(("127.0.0.1", a.listen_port))
            self.poll_until(a, lambda events: a.get_peer("node-b") is not None)
            self.assertEqual(a.get_peer("node-b").node_id, "node-b")  # type: ignore[union-attr]

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
            events_b = self.poll_until(b, lambda events: bool(events.transfers))
            self.assertEqual(len(events_b.transfers), 1)
            self.assertEqual(events_b.transfers[0]["fish_id"], "fish-1")
            events_a = self.poll_until(a, lambda events: transfer_id in events.acked_transfer_ids)
            self.assertIn(transfer_id, events_a.acked_transfer_ids)
            self.assertNotIn(transfer_id, a.pending_transfers)
        finally:
            a.close(announce=False)
            b.close(announce=False)

    def test_hello_registers_peer_role(self) -> None:
        clock = FakeClock()
        admin = self.make_manager(
            "node-admin",
            0,
            broadcast_port=0,
            hostname="admin",
            role=ROLE_ADMIN,
            now_func=clock,
        )
        display = self.make_manager(
            "node-display",
            0,
            broadcast_port=0,
            hostname="display",
            role=ROLE_DISPLAY_NODE,
            now_func=clock,
        )
        try:
            admin.send_hello_to(("127.0.0.1", display.listen_port))
            self.poll_until(display, lambda events: display.get_peer("node-admin") is not None)
            peer = display.get_peer("node-admin")
            self.assertIsNotNone(peer)
            self.assertEqual(peer.role, ROLE_ADMIN)  # type: ignore[union-attr]
            self.assertTrue(peer.is_admin)  # type: ignore[union-attr]
        finally:
            admin.close(announce=False)
            display.close(announce=False)

    def test_admin_command_event_registers_sender_role(self) -> None:
        clock = FakeClock()
        admin = self.make_manager(
            "node-admin",
            0,
            broadcast_port=0,
            hostname="admin",
            role=ROLE_ADMIN,
            now_func=clock,
        )
        display = self.make_manager(
            "node-display",
            0,
            broadcast_port=0,
            hostname="display",
            role=ROLE_DISPLAY_NODE,
            now_func=clock,
        )
        try:
            admin.send_hello_to(("127.0.0.1", display.listen_port))
            self.poll_until(display, lambda events: display.get_peer("node-admin") is not None)
            display.send_hello_to(("127.0.0.1", admin.listen_port))
            self.poll_until(admin, lambda events: admin.get_peer("node-display") is not None)
            admin.send_admin_command("pause", target="node-display")
            events = self.poll_until(display, lambda events: bool(events.admin_commands))
            self.assertEqual(events.admin_commands[0]["action"], "pause")
            self.assertEqual(display.get_peer("node-admin").role, ROLE_ADMIN)  # type: ignore[union-attr]
        finally:
            admin.close(announce=False)
            display.close(announce=False)

    def test_fish_state_broadcast_carries_full_state_and_registers_peer(self) -> None:
        clock = FakeClock()
        receiver = self.make_manager(
            "node-b",
            0,
            broadcast_port=0,
            hostname="receiver",
            now_func=clock,
        )
        sender = self.make_manager(
            "node-a",
            0,
            broadcast_port=receiver.listen_port,
            hostname="sender",
            screen_size=(1024, 768),
            now_func=clock,
        )
        sender.broadcast_targets = ["127.0.0.1"]
        try:
            fishes = [
                {
                    "id": f"fish-{index}",
                    "p": [index / 10, 0.5],
                    "v": [120, index],
                    "s": 50,
                    "c": [1, 2, 3],
                    "d": 0.5,
                    "ph": 0.0,
                }
                for index in range(12)
            ]

            sequence = sender.send_fish_state(len(fishes), fishes)
            events = self.poll_until(receiver, lambda frame: bool(frame.fish_states))

            self.assertEqual(sequence, 1)
            self.assertEqual(len(events.fish_states), 1)
            snapshot = events.fish_states[0]
            self.assertEqual(snapshot["node_id"], "node-a")
            self.assertEqual(snapshot["sequence"], 1)
            self.assertEqual(snapshot["screen_size"], [1024, 768])
            self.assertEqual(len(snapshot["fishes"]), 12)
            peer = receiver.get_peer("node-a")
            self.assertIsNotNone(peer)
            self.assertEqual(peer.screen_size, (1024, 768))  # type: ignore[union-attr]
        finally:
            sender.close(announce=False)
            receiver.close(announce=False)

    def test_legacy_fish_state_sample_only_refreshes_peer(self) -> None:
        from cyberfish.network import NetworkEvents

        manager = self.make_manager("node-b", 0, broadcast_port=0)
        try:
            events = NetworkEvents()
            raw = (
                b'{"type":"fish_state","node_id":"node-a","role":"display_node",'
                b'"fish_count":12,"sample":[{"fish_id":"old"}]}'
            )
            manager._handle_datagram(raw, ("127.0.0.1", 4000), events)

            self.assertIsNotNone(manager.get_peer("node-a"))
            self.assertEqual(events.fish_states, [])
        finally:
            manager.close(announce=False)

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
            manager.close(announce=False)


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
            manager.close(announce=False)

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
            manager.close(announce=False)


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
            # A 的广播能到达 B（这里用定向 DISCOVER 模拟）。
            a.send_hello_to(("127.0.0.1", b.listen_port))

            ok = False
            for _ in range(40):
                b.poll()  # B 发现 A 后单播 DISCOVER_RESPONSE 回 A
                a.poll()  # A 通过单播 DISCOVER_RESPONSE 发现 B
                if a.get_peer("node-b") and b.get_peer("node-a"):
                    ok = True
                    break
                time.sleep(0.02)
            self.assertTrue(ok, "单向广播下应通过单播回复补全双向发现")
        finally:
            a.close(announce=False)
            b.close(announce=False)


if __name__ == "__main__":
    unittest.main()
