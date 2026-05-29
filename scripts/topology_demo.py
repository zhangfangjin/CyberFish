"""单机多实例自动拓扑协商演示。

在同一台机器上启动多个 CyberFish 节点（共用 UDP 端口、走广播互相发现），
驱动 TopologyCoordinator 跑完整的「发现 -> 自动协商 -> 收敛」流程，
并打印每个节点最终的拓扑与互逆一致性检查结果。

无需图形界面，也无需第二台设备，用于快速验证协商逻辑是否跑通。

用法：
    venv/bin/python scripts/topology_demo.py            # 默认 3 个节点
    venv/bin/python scripts/topology_demo.py --nodes 4  # 4 个节点
    venv/bin/python scripts/topology_demo.py --seconds 8 --port 37800
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# 允许直接以脚本方式运行（把项目根目录加入 import 路径）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cyberfish.config import DIRECTIONS, INVERSE_DIRECTIONS, _default_topology
from cyberfish.network import NetworkManager
from cyberfish.topology import TopologyCoordinator


def build_nodes(count: int, port: int, broadcast: str):
    nodes = {}
    for index in range(count):
        node_id = f"node-{chr(ord('a') + index)}"
        net = NetworkManager(node_id, port, broadcast_host=broadcast)
        coord = TopologyCoordinator(node_id, _default_topology(), auto_mode=True)
        nodes[node_id] = (net, coord)
    return nodes


def run(count: int, seconds: float, port: int, broadcast: str, verbose: bool) -> int:
    try:
        nodes = build_nodes(count, port, broadcast)
    except OSError as exc:
        print(f"无法绑定 UDP 端口 {port}: {exc}")
        print("提示：换一个 --port，或确认本机已连接网络（WiFi/局域网）。")
        return 1

    print(f"启动 {count} 个节点，端口 {port}，广播 {broadcast}，运行 {seconds:.0f}s\n")

    deadline = time.monotonic() + seconds
    last_claim = 0.0
    last_print = 0.0
    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            send_claim = now - last_claim >= 1.0
            for net, coord in nodes.values():
                net.send_hello()
                if send_claim:
                    net.send_topology_claim(coord.build_claim_message())
            if send_claim:
                last_claim = now

            for net, coord in nodes.values():
                events = net.poll()
                for claim in events.topology_claims:
                    coord.on_claim(claim)
                online = {peer.node_id for peer in net.sorted_peers()}
                coord.update(online)

            if verbose and now - last_print >= 1.0:
                last_print = now
                online_counts = {
                    nid: len(net.sorted_peers()) for nid, (net, _) in nodes.items()
                }
                print(f"[{now - (deadline - seconds):4.1f}s] 在线主机数: {online_counts}")

            time.sleep(0.1)

        print("\n=== 最终拓扑 ===")
        for nid, (_, coord) in nodes.items():
            cells = "  ".join(
                f"{d}:{coord.topology[d] or '-'}" for d in DIRECTIONS
            )
            print(f"{nid}: {cells}")

        print("\n=== 互逆一致性检查 ===")
        all_ok = True
        for nid, (_, coord) in nodes.items():
            for direction in DIRECTIONS:
                neighbor = coord.topology[direction]
                if not neighbor:
                    continue
                inverse = nodes[neighbor][1].topology[INVERSE_DIRECTIONS[direction]]
                consistent = inverse == nid
                all_ok = all_ok and consistent
                mark = "OK" if consistent else "不一致"
                print(
                    f"{nid}.{direction} = {neighbor}  <->  "
                    f"{neighbor}.{INVERSE_DIRECTIONS[direction]} = {inverse or '-'}  [{mark}]"
                )

        converged = all(coord.is_converged() for _, coord in nodes.values())
        print(
            "\n结果: "
            + ("全部互逆一致" if all_ok else "存在不一致")
            + (" / 已收敛" if converged else " / 未达收敛安静期")
        )
        return 0 if all_ok else 2
    finally:
        for net, _ in nodes.values():
            net.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="单机多实例自动拓扑协商演示")
    parser.add_argument("--nodes", type=int, default=3, help="节点数量，默认 3")
    parser.add_argument("--seconds", type=float, default=6.0, help="运行时长（秒），默认 6")
    parser.add_argument("--port", type=int, default=37800, help="UDP 端口，默认 37800")
    parser.add_argument(
        "--broadcast",
        default="255.255.255.255",
        help="广播地址，默认 255.255.255.255；无外网时可试 127.255.255.255",
    )
    parser.add_argument("--quiet", action="store_true", help="不打印过程中的在线主机数")
    args = parser.parse_args()
    return run(
        count=max(2, args.nodes),
        seconds=args.seconds,
        port=args.port,
        broadcast=args.broadcast,
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    raise SystemExit(main())
