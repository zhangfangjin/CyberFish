"""拓扑协调器（Topology_Coordinator）。

在去中心、对等的前提下，通过 UDP 广播交换的 Negotiation_Message 自动协商屏幕之间
的相邻方向，维护方向互逆一致性与方向唯一性，并在主机动态加入/退出时更新拓扑。

核心算法采用「字典序较小者主导」的成对协商模型（leader-driven pairwise）：

- 对于任意一对节点 (self, peer)，node_id 字典序较小的一方为该方向关系的「主导者」
  (leader)，较大的一方为「跟随者」(follower)。
- 主导者按 left/right/up/down 的固定顺序，把对方分配到第一个空闲方向（先进入
  pending 暂定态），并在 Negotiation_Message 中广播该意图。
- 跟随者收到主导者的广播后，在互逆方向上提交对方为邻居（主导者具有权威性，
  Requirement 4.2 / 3.3）。
- 主导者观察到跟随者已在互逆方向回填自己后，将该方向从 pending 提交为正式邻居
  （Requirement 2.3）。若 10 秒内仍未达成一致，则放弃该 pending 并保持方向为
  null（Requirement 2.4）。

该模型天然满足：方向唯一性（每次只填空方向）、互逆一致性（成对镜像）、
确定性（相同消息集合产生相同结果，Requirement 4.4）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Callable, Iterable

from .config import DIRECTIONS, INVERSE_DIRECTIONS, sanitize_topology


# 暂定分配在多少秒内未达成 Convergence 即放弃（Requirement 2.4 / 5.1）。
ASSIGNMENT_TIMEOUT_SECONDS = 10.0
# Convergence 判定所需的「安静期」：相邻关系连续无变更（Requirement 3.2）。
CONVERGENCE_QUIET_SECONDS = 5.0
# 协商消息的标准类型标识；旧版 "topology" 仅作为接收兼容。
NEGOTIATION_TYPE = "TOPOLOGY_UPDATE"
LEGACY_NEGOTIATION_TYPE = "topology"


@dataclass
class _ManualOverride:
    """一条生效中的手动覆盖（Requirement 9）。"""

    peer_id: str
    # 自手动指定以来是否曾观察到该 Peer 在线；用于区分 9.4（曾在线后离线则清除）
    # 与 9.6（始终离线则保持待确认）。
    seen_online: bool = False


@dataclass
class _Pending:
    """主导者发起的暂定方向分配，等待跟随者确认。"""

    peer_id: str
    since: float


@dataclass
class _Claim:
    topology: dict[str, str | None]
    received_at: float


class TopologyCoordinator:
    def __init__(
        self,
        node_id: str,
        topology: dict[str, str | None],
        *,
        auto_mode: bool = True,
        now_func: Callable[[], float] = time.monotonic,
    ) -> None:
        self.node_id = node_id
        # 直接持有 config.topology 的引用，原地修改即可被跨屏移交逻辑感知
        # （Requirement 8.1）。
        self.topology = topology
        self.auto_mode = bool(auto_mode)
        self.now = now_func

        self.peer_claims: dict[str, _Claim] = {}
        self.manual_overrides: dict[str, _ManualOverride] = {}
        self._pending: dict[str, _Pending] = {}
        self._known_node_ids: set[str] = set()
        self._last_change_at = self.now()

    # ------------------------------------------------------------------
    # 模式开关（Requirement 1）
    # ------------------------------------------------------------------
    def set_auto_mode(self, enabled: bool) -> None:
        """切换自动模式。关闭时停止自动协商并保持当前 Topology 不变（Requirement 1.6）。"""
        enabled = bool(enabled)
        if enabled == self.auto_mode:
            return
        self.auto_mode = enabled
        if not enabled:
            # 关闭时丢弃尚未确认的暂定分配，避免遗留状态。
            self._pending.clear()
        self._last_change_at = self.now()

    # ------------------------------------------------------------------
    # 手动覆盖（Requirement 9）
    # ------------------------------------------------------------------
    def set_manual_override(self, direction: str, peer_id: str) -> tuple[bool, str]:
        """为某方向设置手动覆盖。

        返回 (是否接受, 提示信息)。拒绝条件（Requirement 9.5）：方向非法、
        peer 为本机、或 peer 不是已知节点。
        """
        if direction not in DIRECTIONS:
            return False, f"非法方向: {direction}"
        if peer_id == self.node_id:
            return False, "不能把本机指定为邻居"
        if peer_id not in self._known_node_ids:
            return False, "未知节点，无法指定"

        # 覆盖该方向当前的任意取值，并保证该 Peer 不出现在其它方向（唯一性）。
        self._remove_peer_everywhere(peer_id)
        self.manual_overrides[direction] = _ManualOverride(peer_id=peer_id)
        self.topology[direction] = peer_id
        self._pending.pop(direction, None)
        self._last_change_at = self.now()
        return True, "已设置手动方向"

    def clear_manual_override(self, direction: str) -> None:
        if direction in self.manual_overrides:
            self.manual_overrides.pop(direction, None)
            self.topology[direction] = None
            self._last_change_at = self.now()

    def note_known_peers(self, peer_ids: Iterable[str]) -> None:
        """登记已知（在线或曾在线）节点，作为手动覆盖的合法性判定依据（Requirement 9.5）。"""
        for peer_id in peer_ids:
            if isinstance(peer_id, str) and peer_id:
                self._known_node_ids.add(peer_id)

    # ------------------------------------------------------------------
    # 协商消息收发（Requirement 11）
    # ------------------------------------------------------------------
    def on_claim(self, message: object) -> None:
        """接收并校验一条 Negotiation_Message。

        非法消息将被丢弃且不改变当前 Topology（Requirement 11.3）；
        发送方为本机的消息被忽略（Requirement 11.4）。
        """
        if not isinstance(message, dict):
            return
        if message.get("type") not in (NEGOTIATION_TYPE, LEGACY_NEGOTIATION_TYPE):
            return
        sender = message.get("node_id")
        if not isinstance(sender, str) or not sender:
            return
        if sender == self.node_id:
            return
        raw_topology = message.get("topology")
        if not isinstance(raw_topology, dict):
            return
        self.peer_claims[sender] = _Claim(
            topology=sanitize_topology(raw_topology),
            received_at=self.now(),
        )
        self._known_node_ids.add(sender)

    def build_claim_message(self) -> dict:
        """构造本机要广播的 Negotiation_Message。

        广播的是「意图视图」：已提交邻居 + 尚在 pending 的暂定分配，
        以便跟随者据此回填互逆方向（Requirement 3.1）。
        """
        return {
            "type": NEGOTIATION_TYPE,
            "node_id": self.node_id,
            "topology": self._advertised_topology(),
            "sent_at": self.now(),
        }

    # ------------------------------------------------------------------
    # 主循环：重算拓扑（Requirement 2/3/4/5/6）
    # ------------------------------------------------------------------
    def update(self, online_peer_ids: Iterable[str], now: float | None = None) -> bool:
        """根据在线 Peer 集合与已收到的协商消息重算 Topology。

        返回 Topology（已提交部分）是否发生变化。
        """
        now = self.now() if now is None else now
        online = set(online_peer_ids)
        self._known_node_ids.update(online)

        before = dict(self.topology)

        if not self.auto_mode:
            # 关闭自动模式：停止自动协商，保持 Topology 不变（Requirement 1.6）。
            return False

        # 1. 释放离线邻居（Requirement 6.1）。手动覆盖单独处理。
        for direction in DIRECTIONS:
            neighbor = self.topology[direction]
            if (
                neighbor
                and neighbor not in online
                and direction not in self.manual_overrides
            ):
                self.topology[direction] = None

        # 2. 清理失效或超时的暂定分配（Requirement 2.4）。
        for direction in list(self._pending):
            pending = self._pending[direction]
            if pending.peer_id not in online:
                self._pending.pop(direction, None)
            elif now - pending.since >= ASSIGNMENT_TIMEOUT_SECONDS:
                # 超时仍未 Convergence：放弃，保持方向为 null。
                self._pending.pop(direction, None)
            elif self.topology[direction] is not None:
                # 该方向已被正式占用，pending 失去意义。
                self._pending.pop(direction, None)

        # 3. 手动覆盖（Requirement 9）。
        self._apply_manual_overrides(online)

        # 4. 唯一性约束：同一 Peer 至多出现在一个方向（Requirement 2.5 / 4.1）。
        self._enforce_uniqueness()

        # 5. 跟随者镜像：对字典序小于本机的主导 Peer，依据其声明回填互逆方向
        #    （Requirement 3.1 / 3.3 / 4.2）。
        self._apply_follower_mirrors(online)

        # 6. 确认本机作为主导者发起的暂定分配（Requirement 2.3）。
        self._commit_confirmed_pending(online)

        # 7. 为本机主导的 Peer 发起新的方向分配（Requirement 2.1 / 4.3 / 5.1）。
        self._assign_led_peers(online)

        changed = before != self.topology
        if changed:
            self._last_change_at = now
        return changed

    def is_converged(self, now: float | None = None) -> bool:
        """是否已达成 Convergence（Requirement 3.2 / 11.2）。"""
        now = self.now() if now is None else now
        if self._pending:
            return False
        if now - self._last_change_at < CONVERGENCE_QUIET_SECONDS:
            return False
        for direction in DIRECTIONS:
            neighbor = self.topology[direction]
            if not neighbor:
                continue
            if direction in self.manual_overrides:
                continue
            claim = self.peer_claims.get(neighbor)
            if claim is None:
                return False
            if claim.topology.get(INVERSE_DIRECTIONS[direction]) != self.node_id:
                return False
        return True

    def snapshot(self) -> dict[str, str | None]:
        return dict(self.topology)

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _advertised_topology(self) -> dict[str, str | None]:
        view: dict[str, str | None] = dict(self.topology)
        for direction, pending in self._pending.items():
            if view.get(direction) is None:
                view[direction] = pending.peer_id
        return view

    def _remove_peer_everywhere(self, peer_id: str) -> None:
        for direction in DIRECTIONS:
            if self.topology[direction] == peer_id:
                self.topology[direction] = None
        for direction in list(self._pending):
            if self._pending[direction].peer_id == peer_id:
                self._pending.pop(direction, None)

    def _peer_direction(self, peer_id: str) -> str | None:
        for direction in DIRECTIONS:
            if self.topology[direction] == peer_id:
                return direction
        return None

    def _is_busy(self, peer_id: str) -> bool:
        """该 Peer 是否已占据某个已提交方向或暂定方向。"""
        if self._peer_direction(peer_id) is not None:
            return True
        return any(p.peer_id == peer_id for p in self._pending.values())

    def _apply_manual_overrides(self, online: set[str]) -> None:
        for direction in list(self.manual_overrides):
            override = self.manual_overrides[direction]
            peer_id = override.peer_id
            if peer_id in online:
                override.seen_online = True
                if self.topology[direction] != peer_id:
                    self._remove_peer_everywhere(peer_id)
                    self.topology[direction] = peer_id
            else:
                if override.seen_online:
                    # 曾在线、现连续离线超过 TTL：清除并解除锁定（Requirement 9.4）。
                    self.manual_overrides.pop(direction, None)
                    if self.topology[direction] == peer_id:
                        self.topology[direction] = None
                else:
                    # 始终离线：保持待确认状态（Requirement 9.6）。
                    if self.topology[direction] != peer_id:
                        self.topology[direction] = peer_id

    def _enforce_uniqueness(self) -> None:
        seen: set[str] = set()
        for direction in DIRECTIONS:
            neighbor = self.topology[direction]
            if neighbor is None:
                continue
            if neighbor in seen:
                # 保留 DIRECTIONS 顺序中靠前的方向，清除后续重复。
                self.topology[direction] = None
            else:
                seen.add(neighbor)

    def _apply_follower_mirrors(self, online: set[str]) -> None:
        # 按 node_id 字典序处理主导 Peer，保证较小者优先占用方向（Requirement 4.2）。
        leaders = sorted(
            peer_id
            for peer_id in online
            if peer_id < self.node_id and peer_id in self.peer_claims
        )

        # 第一步：清除孤儿。若某方向当前邻居是一个主导 Peer，但该 Peer 当前的
        # claim 已不再把本机放在对应的互逆方向上，则说明主导者已撤销/改变该分配，
        # 本机应同步释放，避免遗留单边不一致（修复 follower 不收回过期镜像的问题）。
        for direction in DIRECTIONS:
            neighbor = self.topology[direction]
            if not neighbor or neighbor >= self.node_id:
                continue
            if direction in self.manual_overrides:
                continue
            if neighbor not in online:
                continue
            claim = self.peer_claims.get(neighbor)
            if claim is None:
                continue
            # 主导者应在「本机所在方向的互逆方向」上声明本机。
            if claim.topology.get(INVERSE_DIRECTIONS[direction]) != self.node_id:
                self.topology[direction] = None

        # 第二步：按主导者当前 claim 回填互逆方向（Requirement 3.1 / 3.3）。
        for leader in leaders:
            claim = self.peer_claims[leader].topology
            # 找到主导者把本机放在哪个方向。
            leader_direction = next(
                (d for d in DIRECTIONS if claim.get(d) == self.node_id),
                None,
            )
            if leader_direction is None:
                # 主导者当前未声明本机：清除可能遗留的该 Peer 邻居关系。
                self._remove_peer_everywhere(leader)
                continue
            mirror = INVERSE_DIRECTIONS[leader_direction]
            if direction := self._peer_direction(leader):
                if direction == mirror:
                    continue
                # 主导者权威：把邻居迁移到正确的互逆方向。
                self.topology[direction] = None
            if self.topology[mirror] is None and mirror not in self.manual_overrides:
                self._remove_peer_everywhere(leader)
                self.topology[mirror] = leader
                self._pending.pop(mirror, None)

    def _commit_confirmed_pending(self, online: set[str]) -> None:
        for direction in list(self._pending):
            pending = self._pending[direction]
            peer_id = pending.peer_id
            if peer_id not in online:
                continue
            claim = self.peer_claims.get(peer_id)
            if claim is None:
                continue
            # 跟随者已在互逆方向回填本机 → 达成 Convergence，正式提交。
            if claim.topology.get(INVERSE_DIRECTIONS[direction]) == self.node_id:
                if self.topology[direction] is None and direction not in self.manual_overrides:
                    self._remove_peer_everywhere(peer_id)
                    self.topology[direction] = peer_id
                self._pending.pop(direction, None)

    def _assign_led_peers(self, online: set[str]) -> None:
        led_peers = sorted(
            peer_id for peer_id in online if peer_id > self.node_id
        )
        for peer_id in led_peers:
            if self._is_busy(peer_id):
                continue
            claim = self.peer_claims.get(peer_id)
            target = self._first_free_direction(peer_id, claim)
            if target is None:
                # 四个方向均被占用：保持 Topology 不变（Requirement 2.2 / 4.6 / 5.3）。
                continue
            self._pending[target] = _Pending(peer_id=peer_id, since=self.now())

    def _first_free_direction(
        self,
        peer_id: str,
        claim: _Claim | None,
    ) -> str | None:
        """按 left/right/up/down 顺序选第一个可用方向（Requirement 2.1 / 4.3）。

        「可用」要求该方向已提交值为 null、无 pending、非手动锁定，且对方尚未把
        互逆方向分配给别人（避免明显冲突）。
        """
        for direction in DIRECTIONS:
            if self.topology[direction] is not None:
                continue
            if direction in self._pending:
                continue
            if direction in self.manual_overrides:
                continue
            if claim is not None:
                opposite_holder = claim.topology.get(INVERSE_DIRECTIONS[direction])
                if opposite_holder not in (None, self.node_id):
                    continue
            return direction
        return None
