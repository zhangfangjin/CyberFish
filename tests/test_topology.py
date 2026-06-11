from __future__ import annotations

import unittest

from cyberfish.config import DIRECTIONS, INVERSE_DIRECTIONS, _default_topology
from cyberfish.topology import (
    ASSIGNMENT_TIMEOUT_SECONDS,
    CONVERGENCE_QUIET_SECONDS,
    LEGACY_NEGOTIATION_TYPE,
    NEGOTIATION_TYPE,
    TopologyCoordinator,
)


class FakeClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def make_coordinator(node_id: str, clock: FakeClock, *, auto: bool = True) -> TopologyCoordinator:
    return TopologyCoordinator(
        node_id=node_id,
        topology=_default_topology(),
        auto_mode=auto,
        now_func=clock,
    )


def converge(
    a: TopologyCoordinator,
    b: TopologyCoordinator,
    clock: FakeClock,
    *,
    rounds: int = 8,
    step: float = 1.0,
) -> None:
    """模拟两台主机互相广播协商消息直至收敛。"""
    a_online = {b.node_id}
    b_online = {a.node_id}
    for _ in range(rounds):
        a.on_claim(b.build_claim_message())
        b.on_claim(a.build_claim_message())
        a.update(a_online)
        b.update(b_online)
        clock.advance(step)


class AutoModeTests(unittest.TestCase):
    def test_set_auto_mode_off_freezes_topology(self) -> None:
        clock = FakeClock()
        coord = make_coordinator("node-a", clock)
        coord.topology["right"] = "node-b"
        coord.set_auto_mode(False)
        # 关闭后即使有在线 peer 也不应改写已有方向。
        changed = coord.update({"node-z"})
        self.assertFalse(changed)
        self.assertEqual(coord.topology["right"], "node-b")


class DirectionAssignmentTests(unittest.TestCase):
    def test_pair_converges_to_inverse_consistent_topology(self) -> None:
        clock = FakeClock()
        a = make_coordinator("node-a", clock)
        b = make_coordinator("node-b", clock)
        converge(a, b, clock)

        # node-a 字典序较小，作为主导者按 left 优先分配 node-b。
        self.assertEqual(a.topology["left"], "node-b")
        # 互逆一致：node-b 在 right 上回填 node-a。
        self.assertEqual(b.topology["right"], "node-a")

    def test_peer_appears_in_at_most_one_direction(self) -> None:
        clock = FakeClock()
        a = make_coordinator("node-a", clock)
        b = make_coordinator("node-b", clock)
        converge(a, b, clock)
        occurrences = sum(1 for d in DIRECTIONS if a.topology[d] == "node-b")
        self.assertEqual(occurrences, 1)

    def test_no_assignment_when_all_directions_busy(self) -> None:
        clock = FakeClock()
        coord = make_coordinator("node-a", clock)
        for direction in DIRECTIONS:
            coord.topology[direction] = f"peer-{direction}"
        # 一个字典序更大的新 peer 上线，但四个方向全满 → 拓扑不变。
        before = dict(coord.topology)
        coord.update({f"peer-{d}" for d in DIRECTIONS} | {"node-zzz"})
        self.assertEqual(before, coord.topology)

    def test_pending_times_out_without_confirmation(self) -> None:
        clock = FakeClock()
        a = make_coordinator("node-a", clock)
        # node-b 在线但从不回应（无 claim）。
        for _ in range(3):
            a.update({"node-b"})
            clock.advance(1.0)
        # 暂定分配尚未提交。
        self.assertIsNone(a.topology["left"])
        clock.advance(ASSIGNMENT_TIMEOUT_SECONDS)
        a.update({"node-b"})
        # 超时后放弃，方向保持 null。
        self.assertIsNone(a.topology["left"])


class ConflictResolutionTests(unittest.TestCase):
    def _run_mesh(self, node_ids: list[str], clock: FakeClock, rounds: int = 25) -> dict:
        coords = {nid: make_coordinator(nid, clock) for nid in node_ids}
        for _ in range(rounds):
            for coord in coords.values():
                for other_id, other in coords.items():
                    if other_id != coord.node_id:
                        coord.on_claim(other.build_claim_message())
            for coord in coords.values():
                online = {nid for nid in coords if nid != coord.node_id}
                coord.update(online)
            clock.advance(1.0)
        return coords

    def _assert_consistent(self, coords: dict) -> None:
        # 每台主机的方向取值唯一（无重复 peer）。
        for coord in coords.values():
            values = [coord.topology[d] for d in DIRECTIONS if coord.topology[d]]
            self.assertEqual(len(values), len(set(values)))
        # 互逆一致性：a 在方向 D 上的邻居 P，P 必在 D 的互逆方向回填 a；
        # 且不能出现单边孤儿（一方记了邻居，另一方为空）。
        for coord in coords.values():
            for direction in DIRECTIONS:
                neighbor = coord.topology[direction]
                if neighbor:
                    self.assertEqual(
                        coords[neighbor].topology[INVERSE_DIRECTIONS[direction]],
                        coord.node_id,
                        f"{coord.node_id}.{direction}={neighbor} 出现单边孤儿",
                    )

    def test_three_nodes_reach_unique_directions(self) -> None:
        clock = FakeClock()
        coords = self._run_mesh(["node-a", "node-b", "node-c"], clock)
        self._assert_consistent(coords)

    def test_four_nodes_have_no_orphan_neighbors(self) -> None:
        # 回归测试：4 节点曾出现 follower 不收回过期镜像导致的单边孤儿。
        clock = FakeClock()
        coords = self._run_mesh(["node-a", "node-b", "node-c", "node-d"], clock)
        self._assert_consistent(coords)


class DynamicMembershipTests(unittest.TestCase):
    def test_offline_neighbor_direction_is_released(self) -> None:
        clock = FakeClock()
        a = make_coordinator("node-a", clock)
        b = make_coordinator("node-b", clock)
        converge(a, b, clock)
        self.assertEqual(a.topology["left"], "node-b")

        # node-b 离线（不在在线集合中）。
        a.update(set())
        self.assertIsNone(a.topology["left"])

    def test_rediscovered_peer_is_reassigned(self) -> None:
        clock = FakeClock()
        a = make_coordinator("node-a", clock)
        b = make_coordinator("node-b", clock)
        converge(a, b, clock)
        a.update(set())
        self.assertIsNone(a.topology["left"])

        # node-b 重新上线，重新收敛。
        b2 = make_coordinator("node-b", clock)
        converge(a, b2, clock)
        self.assertEqual(a.topology["left"], "node-b")


class ConvergenceTests(unittest.TestCase):
    def test_is_converged_after_quiet_period(self) -> None:
        clock = FakeClock()
        a = make_coordinator("node-a", clock)
        b = make_coordinator("node-b", clock)
        converge(a, b, clock)
        clock.advance(CONVERGENCE_QUIET_SECONDS + 1.0)
        # 再交换一轮 claim 以保证彼此看到最新互逆视图。
        a.on_claim(b.build_claim_message())
        b.on_claim(a.build_claim_message())
        a.update({"node-b"})
        b.update({"node-a"})
        self.assertTrue(a.is_converged())
        self.assertTrue(b.is_converged())


class ManualOverrideTests(unittest.TestCase):
    def test_manual_override_rejects_self_and_unknown(self) -> None:
        clock = FakeClock()
        coord = make_coordinator("node-a", clock)
        ok, _ = coord.set_manual_override("left", "node-a")
        self.assertFalse(ok)
        ok, _ = coord.set_manual_override("left", "node-unknown")
        self.assertFalse(ok)
        self.assertIsNone(coord.topology["left"])

    def test_manual_override_locks_direction_against_auto(self) -> None:
        clock = FakeClock()
        coord = make_coordinator("node-a", clock)
        coord.note_known_peers(["node-x"])
        ok, _ = coord.set_manual_override("left", "node-x")
        self.assertTrue(ok)
        self.assertEqual(coord.topology["left"], "node-x")

        # 字典序更大的新 peer 上线，但 left 被手动锁定，应分配到 right。
        coord.update({"node-x", "node-zzz"})
        self.assertEqual(coord.topology["left"], "node-x")

    def test_manual_override_released_after_seen_then_offline(self) -> None:
        clock = FakeClock()
        coord = make_coordinator("node-a", clock)
        coord.note_known_peers(["node-x"])
        coord.set_manual_override("left", "node-x")
        # 先在线确认。
        coord.update({"node-x"})
        self.assertEqual(coord.topology["left"], "node-x")
        # 随后离线 → 解除锁定并清空方向（Requirement 9.4）。
        coord.update(set())
        self.assertIsNone(coord.topology["left"])
        self.assertNotIn("left", coord.manual_overrides)

    def test_manual_override_pending_when_never_online(self) -> None:
        clock = FakeClock()
        coord = make_coordinator("node-a", clock)
        coord.note_known_peers(["node-x"])
        coord.set_manual_override("left", "node-x")
        # 从未在线：保持待确认（Requirement 9.6）。
        coord.update(set())
        self.assertEqual(coord.topology["left"], "node-x")
        self.assertIn("left", coord.manual_overrides)


class MessageValidationTests(unittest.TestCase):
    def test_invalid_messages_are_ignored(self) -> None:
        clock = FakeClock()
        coord = make_coordinator("node-a", clock)
        coord.on_claim("not a dict")
        coord.on_claim({"type": "hello", "node_id": "node-b"})
        coord.on_claim({"type": NEGOTIATION_TYPE})  # 缺 node_id
        coord.on_claim({"type": LEGACY_NEGOTIATION_TYPE, "node_id": "node-a", "topology": {}})  # 本机
        self.assertEqual(coord.peer_claims, {})

    def test_standard_and_legacy_claim_messages_are_accepted(self) -> None:
        clock = FakeClock()
        coord = make_coordinator("node-a", clock)
        coord.on_claim({"type": NEGOTIATION_TYPE, "node_id": "node-b", "topology": {"right": "node-a"}})
        coord.on_claim({"type": LEGACY_NEGOTIATION_TYPE, "node_id": "node-c", "topology": {"left": "node-a"}})
        self.assertIn("node-b", coord.peer_claims)
        self.assertIn("node-c", coord.peer_claims)

    def test_self_message_ignored(self) -> None:
        clock = FakeClock()
        coord = make_coordinator("node-a", clock)
        coord.on_claim({"type": NEGOTIATION_TYPE, "node_id": "node-a", "topology": {"right": "node-b"}})
        self.assertNotIn("node-a", coord.peer_claims)

    def test_claim_message_within_datagram_limit(self) -> None:
        import json
        from cyberfish.network import MAX_DATAGRAM_BYTES

        clock = FakeClock()
        coord = make_coordinator("node-a", clock)
        for direction in DIRECTIONS:
            coord.topology[direction] = f"node-{direction}-xxxxxxxx"
        payload = json.dumps(coord.build_claim_message()).encode("utf-8")
        self.assertLessEqual(len(payload), MAX_DATAGRAM_BYTES)


if __name__ == "__main__":
    unittest.main()
