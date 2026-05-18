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

    @property
    def address(self) -> tuple[str, int]:
        return self._socket.getsockname()

    def close(self) -> None:
        self._socket.close()

    def update_screen_size(self, screen_size: tuple[int, int]) -> None:
        self.screen_size = screen_size

    def send_hello(self) -> None:
        self._send_message(
            self._hello_message(),
            (self.broadcast_host, int(self.broadcast_port or self.listen_port)),
        )

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
        self._send_message(message, (self.broadcast_host, int(self.broadcast_port or self.listen_port)))

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
            return

        message_type = message.get("type")
        if message_type == "hello":
            self._handle_hello(message, address, events)
        elif message_type == "fish_transfer":
            self._handle_fish_transfer(message, address, events)
        elif message_type == "transfer_ack":
            self._handle_transfer_ack(message, events)
        elif message_type == "fish_state":
            self._handle_fish_state(message, address, events)

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
        peer = Peer(
            node_id=node_id,
            hostname=str(message.get("hostname") or node_id),
            address=address[0],
            port=port,
            screen_size=size,
            last_seen=self.now(),
        )
        is_new = node_id not in self.peers
        self.peers[node_id] = peer
        if is_new:
            events.discovered.append(peer)

    def _handle_fish_state(
        self,
        message: dict,
        address: tuple[str, int],
        events: NetworkEvents,
    ) -> None:
        if message.get("node_id") not in self.peers:
            self._handle_hello(
                {
                    "type": "hello",
                    "node_id": message.get("node_id"),
                    "hostname": message.get("hostname") or message.get("node_id"),
                    "port": address[1],
                    "screen_size": [0, 0],
                },
                address,
                events,
            )

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
