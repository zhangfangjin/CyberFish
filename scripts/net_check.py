"""局域网 UDP 连通性自检工具（多设备测试前先用它确认广播能互通）。

多设备测试最常见的失败原因不是程序，而是网络：防火墙拦截、WiFi 的 AP 隔离
（客户端互相不可见）、或不在同一子网。本工具复用 CyberFish 的端口/广播设置，
帮你在真正启动程序前快速定位问题。

用法（两台机器，同一局域网）：

  机器 A（监听）:
      venv/bin/python scripts/net_check.py listen

  机器 B（发送）:
      venv/bin/python scripts/net_check.py send

机器 A 若持续打印收到来自机器 B 的报文，说明广播互通，可以直接跑程序。
若收不到，多半是防火墙或 AP 隔离，见脚本末尾的排障提示。

也可在单机两个终端各跑一次（listen / send）做本地自检。
"""

from __future__ import annotations

import argparse
import socket
import time


def make_socket(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass
    return sock


def local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "?"
    finally:
        sock.close()


def do_listen(port: int) -> int:
    sock = make_socket(port)
    try:
        sock.bind(("", port))
    except OSError as exc:
        print(f"绑定端口 {port} 失败: {exc}")
        return 1
    print(f"[监听] 本机 {local_ip()} 在 UDP :{port} 等待报文，Ctrl+C 退出\n")
    count = 0
    try:
        while True:
            data, addr = sock.recvfrom(2048)
            count += 1
            print(f"  收到 #{count} 来自 {addr[0]}:{addr[1]} -> {data.decode('utf-8', 'replace')}")
    except KeyboardInterrupt:
        print(f"\n共收到 {count} 条报文。")
        return 0 if count else 2
    finally:
        sock.close()


def do_send(port: int, broadcast: str, count: int) -> int:
    sock = make_socket(port)
    ip = local_ip()
    print(f"[发送] 本机 {ip} 向 {broadcast}:{port} 广播 {count} 条，每秒 1 条\n")
    try:
        for index in range(1, count + 1):
            message = f"net_check from {ip} seq={index}"
            try:
                sock.sendto(message.encode("utf-8"), (broadcast, port))
                print(f"  已发送 #{index}: {message}")
            except OSError as exc:
                print(f"  发送 #{index} 失败: {exc}（检查网络连接/广播地址）")
            time.sleep(1.0)
    finally:
        sock.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="局域网 UDP 连通性自检")
    parser.add_argument("mode", choices=["listen", "send"], help="listen=监听, send=发送")
    parser.add_argument("--port", type=int, default=37777, help="UDP 端口，需与 config.json 的 udp_port 一致，默认 37777")
    parser.add_argument("--broadcast", default="255.255.255.255", help="发送模式的广播地址，默认 255.255.255.255")
    parser.add_argument("--count", type=int, default=10, help="发送模式的报文条数，默认 10")
    args = parser.parse_args()

    if args.mode == "listen":
        return do_listen(args.port)
    return do_send(args.port, args.broadcast, args.count)


if __name__ == "__main__":
    raise SystemExit(main())
