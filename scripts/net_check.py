"""局域网 UDP 连通性自检工具（多设备测试前先用它确认广播能互通）。

多设备测试最常见的失败原因不是程序，而是网络：防火墙拦截、WiFi 的 AP 隔离
（客户端互相不可见）、不在同一子网，或两台设备 node_id 相同（拷贝了 config.json）。
本工具复用 CyberFish 的端口/广播策略，帮你在启动程序前快速定位问题。

推荐用法（两台机器各跑一次，同时收发）：

    机器 A:  venv/bin/python scripts/net_check.py
    机器 B:  venv/bin/python scripts/net_check.py

每台机器会每秒广播一次自报家门，同时打印收到的对方报文。
若双方都能看到对方的 IP，说明广播互通，可以直接跑程序。

也保留单向模式用于精细排查：
    venv/bin/python scripts/net_check.py listen   # 只监听
    venv/bin/python scripts/net_check.py send     # 只发送
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cyberfish.network import detect_local_ip, subnet_broadcast_for


def make_socket(port: int, bind: bool) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass
    if bind:
        sock.bind(("", port))
    sock.setblocking(False)
    return sock


def broadcast_targets(extra: str | None) -> list[str]:
    ip = detect_local_ip()
    targets: list[str] = []
    for host in (extra, subnet_broadcast_for(ip), "255.255.255.255"):
        if host and host not in targets:
            targets.append(host)
    return targets


def print_header(port: int, targets: list[str]) -> str:
    ip = detect_local_ip() or "?"
    print(f"本机 IP: {ip}    主机名: {socket.gethostname()}")
    print(f"UDP 端口: {port}    广播目标: {', '.join(targets)}")
    print("-" * 60)
    return ip


def run_duplex(port: int, extra_broadcast: str | None, seconds: float | None) -> int:
    targets = broadcast_targets(extra_broadcast)
    ip = print_header(port, targets)
    tag = uuid.uuid4().hex[:6]  # 区分本机自己的回环报文
    sock = make_socket(port, bind=True)

    seen_peers: set[str] = set()
    deadline = None if seconds is None else time.monotonic() + seconds
    last_send = 0.0
    seq = 0
    print("开始收发，Ctrl+C 退出\n")
    try:
        while deadline is None or time.monotonic() < deadline:
            now = time.monotonic()
            if now - last_send >= 1.0:
                seq += 1
                msg = f"netcheck|{ip}|{socket.gethostname()}|{tag}|{seq}"
                for host in targets:
                    try:
                        sock.sendto(msg.encode("utf-8"), (host, port))
                    except OSError as exc:
                        print(f"  发送到 {host} 失败: {exc}")
                last_send = now
            # 收
            while True:
                try:
                    data, addr = sock.recvfrom(2048)
                except BlockingIOError:
                    break
                except OSError:
                    break
                text = data.decode("utf-8", "replace")
                if text.startswith("netcheck|") and f"|{tag}|" in text:
                    continue  # 跳过自己的报文
                parts = text.split("|")
                peer_ip = parts[1] if len(parts) > 1 else addr[0]
                peer_host = parts[2] if len(parts) > 2 else "?"
                key = f"{peer_ip} ({peer_host})"
                if key not in seen_peers:
                    seen_peers.add(key)
                    print(f"  ✅ 发现对端: {key}  来自 {addr[0]}:{addr[1]}")
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
    print(f"\n共发现 {len(seen_peers)} 个对端: {', '.join(sorted(seen_peers)) or '无'}")
    if not seen_peers:
        print(
            "\n未发现任何对端，排查方向：\n"
            "  1. 两台设备是否在同一 WiFi/子网（IP 前三段应相同）\n"
            "  2. WiFi 是否开启了 AP 隔离（访客网络常见）→ 换热点或路由器\n"
            "  3. 防火墙是否放行 Python / 该 UDP 端口\n"
            "  4. 端口是否一致（两台都用同一个 --port）"
        )
        return 2
    return 0


def run_listen(port: int) -> int:
    targets = broadcast_targets(None)
    print_header(port, targets)
    sock = make_socket(port, bind=True)
    print("[监听] 等待报文，Ctrl+C 退出\n")
    count = 0
    try:
        while True:
            try:
                data, addr = sock.recvfrom(2048)
            except BlockingIOError:
                time.sleep(0.05)
                continue
            count += 1
            print(f"  收到 #{count} 来自 {addr[0]}:{addr[1]} -> {data.decode('utf-8', 'replace')}")
    except KeyboardInterrupt:
        print(f"\n共收到 {count} 条报文。")
        return 0 if count else 2
    finally:
        sock.close()


def run_send(port: int, extra_broadcast: str | None, count: int) -> int:
    targets = broadcast_targets(extra_broadcast)
    ip = print_header(port, targets)
    sock = make_socket(port, bind=False)
    print(f"[发送] 广播 {count} 条，每秒 1 条\n")
    try:
        for index in range(1, count + 1):
            message = f"netcheck|{ip}|{socket.gethostname()}|send|{index}"
            for host in targets:
                try:
                    sock.sendto(message.encode("utf-8"), (host, port))
                except OSError as exc:
                    print(f"  发送到 {host} 失败: {exc}")
            print(f"  已发送 #{index} -> {targets}")
            time.sleep(1.0)
    finally:
        sock.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="局域网 UDP 连通性自检")
    parser.add_argument(
        "mode",
        nargs="?",
        default="duplex",
        choices=["duplex", "listen", "send"],
        help="duplex=同时收发(默认), listen=只监听, send=只发送",
    )
    parser.add_argument("--port", type=int, default=37777, help="UDP 端口，需与 config.json 的 udp_port 一致，默认 37777")
    parser.add_argument("--broadcast", default=None, help="额外的广播地址（默认自动推导子网广播 + 255.255.255.255）")
    parser.add_argument("--count", type=int, default=10, help="send 模式发送条数，默认 10")
    parser.add_argument("--seconds", type=float, default=None, help="duplex 模式运行时长（秒），默认一直运行")
    args = parser.parse_args()

    if args.mode == "listen":
        return run_listen(args.port)
    if args.mode == "send":
        return run_send(args.port, args.broadcast, args.count)
    return run_duplex(args.port, args.broadcast, args.seconds)


if __name__ == "__main__":
    raise SystemExit(main())
