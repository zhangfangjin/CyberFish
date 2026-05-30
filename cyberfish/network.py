from __future__ import annotations

from dataclasses import dataclass, field
import json
import socket
import time
from typing import Callable
import uuid


DISCOVERY_TTL_SECONDS = 8.0
TRANSFER_RETRY_SECONDS = 0.06
TRANSFER_TIMEOUT_SECONDS = 0.75
RECEIVED_TRANSFER_TTL_SECONDS = 20.0
MAX_DATAGRAM_BYTES = 65507


def detect_local_ip() -> str | None:
    """探测本机在局域网中的出口 IP（不会真正发包）。失败返回 None。"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def subnet_broadcast_for(ip: str | None) -> str | None:
    """由本机 IP 推导出 /24 子网的定向广播地址，如 10.0.0.91 -> 10.0.0.255。

    子网定向广播在 macOS 多网卡环境下比受限广播 255.255.255.255 更可靠。
    仅对常见的私有 /24 网段做简单推导，无法判断时返回 None。
    """
    if not ip:
        return None
    parts = ip.split(".")
    if len(parts) != 4:
        return None
    try:
        if any(not (0 <= int(p) <= 255) for p in parts):
            return None
    except ValueError:
        return None
    if ip.startswith("127."):
        return None
    return f"{parts[0]}.{parts[1]}.{parts[2]}.255"


@dataclass
class Peer:
    node_id: str
    hostname: str
    address: str
    port: int
    screen_size: tuple[int, int]
    last_seen: float


@dataclass
class PendingTransfer:
    message: dict
    address: tuple[str, int]
    fish_payload: dict
    created_at: float
    last_sent: float = 0.0
    attempts: int = 0


@dataclass
class NetworkEvents:
    transfers: list[dict] = field(default_factory=list)
    expired_transfers: list[dict] = field(default_factory=list)
    discovered: list[Peer] = field(default_factory=list)
    acked_transfer_ids: list[str] = field(default_factory=list)
    topology_claims: list[dict] = field(default_factory=list)
    # 检测到与本机相同 node_id 但来自其它主机的报文时置 True（node_id 冲突）。
    node_id_conflict: bool = False


class NetworkManager:
    def __init__(
        self,
        node_id: str,
        listen_port: int,
        *,
        broadcast_host: str = "255.255.255.255",
        broadcast_port: int | None = None,
        bind_host: str = "",
        hostname: str | None = None,
        screen_size: tuple[int, int] = (0, 0),
        now_func: Callable[[], float] = time.monotonic,
    ) -> None:
        self.node_id = node_id
        self.listen_port = int(listen_port)
        self.broadcast_host = broadcast_host
        self.broadcast_port = broadcast_port
        self.bind_host = bind_host
        self.hostname = hostname or socket.gethostname()
        self.screen_size = screen_size
        self.now = now_func
        self.peers: dict[str, Peer] = {}
        self.pending_transfers: dict[str, PendingTransfer] = {}
        self._received_transfers: dict[str, float] = {}
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        try:
            self._socket.bind((self.bind_host, self.listen_port))
        except OSError:
            self._socket.close()
            raise
        self._socket.setblocking(False)
        self.listen_port = self._socket.getsockname()[1]
        if self.broadcast_port is None or self.broadcast_port == 0:
            self.broadcast_port = self.listen_port
        self.local_ip = detect_local_ip()
        self.broadcast_targets = self._build_broadcast_targets()

    def _build_broadcast_targets(self) -> list[str]:
        """构造广播目标地址列表：用户配置地址 + 子网定向广播。

        子网定向广播（如 10.0.0.255）在 macOS 多网卡/WiFi 环境下比受限广播
        255.255.255.255 更可靠，两者都发可显著提升真机互相发现的成功率。
        """
        targets: list[str] = []

        def add(host: str | None) -> None:
            if host and host not in targets:
                targets.append(host)

        add(self.broadcast_host)
        add(subnet_broadcast_for(self.local_ip))
        # 始终兜底加入受限广播。
        add("255.255.255.255")
        return targets

    def _broadcast(self, message: dict) -> None:
        port = int(self.broadcast_port or self.listen_port)
        for host in self.broadcast_targets:
            self._send_message(message, (host, port))

    @property
    def address(self) -> tuple[str, int]:
        return self._socket.getsockname()

    def close(self) -> None:
        self._socket.close()

    def update_screen_size(self, screen_size: tuple[int, int]) -> None:
        self.screen_size = screen_size

    def send_hello(self) -> None:
        self._broadcast(self._hello_message())

    def send_hello_to(self, address: tuple[str, int]) -> None:
        self._send_message(self._hello_message(), address)

    def send_fish_state(self, fish_count: int, sample: list[dict] | None = None) -> None:
        message = {
            "type": "fish_state",
            "node_id": self.node_id,
            "sent_at": self.now(),
            "fish_count": fish_count,
            "sample": sample or [],
        }
        self._broadcast(message)

    def send_topology_claim(self, message: dict) -> None:
        """广播一条拓扑协商消息（Negotiation_Message，Requirement 11.1/11.5）。"""
        payload = dict(message)
        payload["node_id"] = self.node_id
        self._broadcast(payload)

    def send_fish_transfer(self, peer: Peer, fish_payload: dict) -> str:
        transfer_id = f"{self.node_id}-{uuid.uuid4().hex}"
        message = {
            "type": "fish_transfer",
            "node_id": self.node_id,
            "target_node_id": peer.node_id,
            "transfer_id": transfer_id,
            "sent_at": self.now(),
            "fish": fish_payload,
        }
        pending = PendingTransfer(
            message=message,
            address=(peer.address, peer.port),
            fish_payload=fish_payload,
            created_at=self.now(),
        )
        self.pending_transfers[transfer_id] = pending
        self._send_pending(pending)
        return transfer_id

    def poll(self) -> NetworkEvents:
        events = NetworkEvents()
        while True:
            try:
                raw, address = self._socket.recvfrom(MAX_DATAGRAM_BYTES)
            except BlockingIOError:
                break
            except OSError:
                break
            self._handle_datagram(raw, address, events)

        self._retry_or_expire_pending(events)
        self._drop_stale_peers()
        self._drop_old_transfer_ids()
        return events

    def get_peer(self, node_id: str | None) -> Peer | None:
        if not node_id:
            return None
        return self.peers.get(node_id)

    def sorted_peers(self) -> list[Peer]:
        return sorted(self.peers.values(), key=lambda peer: (peer.hostname, peer.node_id))

    def _hello_message(self) -> dict:
        return {
            "type": "hello",
            "node_id": self.node_id,
            "hostname": self.hostname,
            "port": self.listen_port,
            "screen_size": [self.screen_size[0], self.screen_size[1]],
            "sent_at": self.now(),
        }

    def _handle_datagram(
        self,
        raw: bytes,
        address: tuple[str, int],
        events: NetworkEvents,
    ) -> None:
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(message, dict):
            return
        if message.get("node_id") == self.node_id:
            # 收到与本机相同 node_id 的报文。若来源 IP 不是本机，说明局域网中
            # 另有主机使用了相同 node_id（通常是直接拷贝了 config.json），
            # 这会导致双方互相忽略对方、永远无法发现。标记冲突以便上层提示用户。
            sender_ip = address[0]
            if (
                self.local_ip
                and sender_ip not in ("127.0.0.1", self.local_ip)
            ):
                events.node_id_conflict = True
            return

        message_type = message.get("type")
        if message_type == "hello":
            self._handle_hello(message, address, events)
        elif message_type == "fish_transfer":
            self._handle_fish_transfer(message, address, events)
        elif message_type == "transfer_ack":
            self._handle_transfer_ack(message, events)
            self._register_peer_from_message(message, address, events)
        elif message_type == "fish_state":
            self._handle_fish_state(message, address, events)
        elif message_type == "topology":
            events.topology_claims.append(message)
            self._register_peer_from_message(message, address, events)

    def _register_peer(
        self,
        node_id: str,
        address: tuple[str, int],
        events: NetworkEvents,
        *,
        port: int | None = None,
        hostname: str | None = None,
        screen_size: tuple[int, int] | None = None,
    ) -> bool:
        """登记或刷新一个 Peer，返回是否为新发现。

        任何携带 node_id 的报文都会让对端被登记，使得即便某一方向的广播不可达
        （例如 Windows 多网卡只从虚拟网卡发出 255.255.255.255），也能通过收到的
        单播报文学习到对端，从而补全双向发现。
        """
        if not node_id or node_id == self.node_id:
            return False
        existing = self.peers.get(node_id)
        peer = Peer(
            node_id=node_id,
            hostname=hostname or (existing.hostname if existing else node_id),
            address=address[0],
            port=int(port or address[1]),
            screen_size=screen_size
            or (existing.screen_size if existing else (0, 0)),
            last_seen=self.now(),
        )
        is_new = existing is None
        self.peers[node_id] = peer
        if is_new:
            events.discovered.append(peer)
            # 发现新对端后立即单播回一条 hello，确保反向发现不依赖广播可达性。
            self.send_hello_to((peer.address, peer.port))
        return is_new

    def _register_peer_from_message(
        self,
        message: dict,
        address: tuple[str, int],
        events: NetworkEvents,
    ) -> None:
        node_id = str(message.get("node_id") or "")
        if node_id:
            self._register_peer(node_id, address, events)

    def _handle_hello(
        self,
        message: dict,
        address: tuple[str, int],
        events: NetworkEvents,
    ) -> None:
        node_id = str(message.get("node_id") or "")
        if not node_id:
            return
        port = int(message.get("port") or address[1])
        screen_size = message.get("screen_size") or [0, 0]
        try:
            size = (int(screen_size[0]), int(screen_size[1]))
        except (TypeError, ValueError, IndexError):
            size = (0, 0)
        self._register_peer(
            node_id,
            address,
            events,
            port=port,
            hostname=str(message.get("hostname") or node_id),
            screen_size=size,
        )

    def _handle_fish_state(
        self,
        message: dict,
        address: tuple[str, int],
        events: NetworkEvents,
    ) -> None:
        self._register_peer_from_message(message, address, events)

    def _handle_fish_transfer(
        self,
        message: dict,
        address: tuple[str, int],
        events: NetworkEvents,
    ) -> None:
        if message.get("target_node_id") != self.node_id:
            return
        transfer_id = str(message.get("transfer_id") or "")
        fish_payload = message.get("fish")
        if not transfer_id or not isinstance(fish_payload, dict):
            return

        # 收到鱼移交说明对端能单播到本机；登记其地址以补全反向发现
        # （即便对端的广播 hello 到不了本机）。
        self._register_peer_from_message(message, address, events)

        self._send_message(
            {
                "type": "transfer_ack",
                "node_id": self.node_id,
                "target_node_id": message.get("node_id"),
                "transfer_id": transfer_id,
                "sent_at": self.now(),
            },
            address,
        )
        if transfer_id in self._received_transfers:
            return
        self._received_transfers[transfer_id] = self.now()
        events.transfers.append(fish_payload)

    def _handle_transfer_ack(self, message: dict, events: NetworkEvents) -> None:
        if message.get("target_node_id") != self.node_id:
            return
        transfer_id = str(message.get("transfer_id") or "")
        if transfer_id in self.pending_transfers:
            self.pending_transfers.pop(transfer_id, None)
            events.acked_transfer_ids.append(transfer_id)

    def _retry_or_expire_pending(self, events: NetworkEvents) -> None:
        now = self.now()
        expired: list[str] = []
        for transfer_id, pending in list(self.pending_transfers.items()):
            if now - pending.created_at >= TRANSFER_TIMEOUT_SECONDS:
                events.expired_transfers.append(pending.fish_payload)
                expired.append(transfer_id)
                continue
            if now - pending.last_sent >= TRANSFER_RETRY_SECONDS:
                self._send_pending(pending)
        for transfer_id in expired:
            self.pending_transfers.pop(transfer_id, None)

    def _drop_stale_peers(self) -> None:
        now = self.now()
        stale = [
            node_id
            for node_id, peer in self.peers.items()
            if now - peer.last_seen > DISCOVERY_TTL_SECONDS
        ]
        for node_id in stale:
            self.peers.pop(node_id, None)

    def _drop_old_transfer_ids(self) -> None:
        now = self.now()
        old = [
            transfer_id
            for transfer_id, seen_at in self._received_transfers.items()
            if now - seen_at > RECEIVED_TRANSFER_TTL_SECONDS
        ]
        for transfer_id in old:
            self._received_transfers.pop(transfer_id, None)

    def _send_pending(self, pending: PendingTransfer) -> None:
        pending.last_sent = self.now()
        pending.attempts += 1
        self._send_message(pending.message, pending.address)

    def _send_message(self, message: dict, address: tuple[str, int]) -> None:
        try:
            payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
            self._socket.sendto(payload, address)
        except OSError:
            return
