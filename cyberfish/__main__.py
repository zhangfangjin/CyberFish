from __future__ import annotations

import argparse
import os
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cyberfish",
        description="CyberFish Pygame 局域网水族箱",
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="JSON 配置文件路径，默认 ./config.json。",
    )
    parser.add_argument(
        "--headless-smoke",
        action="store_true",
        help="使用 SDL dummy 驱动运行自动化冒烟检查。",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="可选的最大运行时长，单位为秒。",
    )
    parser.add_argument(
        "--no-network",
        action="store_true",
        help="本次运行禁用 UDP 发现和鱼跨屏移交。",
    )
    role_group = parser.add_mutually_exclusive_group()
    role_group.add_argument(
        "--admin",
        action="store_true",
        help="本次运行临时作为管理员主机启动。",
    )
    role_group.add_argument(
        "--display-node",
        action="store_true",
        help="本次运行临时作为演示节点启动。",
    )
    parser.add_argument(
        "--debug-net",
        action="store_true",
        help="在屏幕右上角叠加显示网络收发统计，并在控制台打印诊断日志，用于排查多机连接问题。",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.headless_smoke:
        # 冒烟测试在无窗口/无音频设备环境运行，必须在导入 pygame 相关模块前设置 SDL。
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

    from .app import CyberFishApp
    from .config import ROLE_ADMIN, ROLE_DISPLAY_NODE

    role_override = None
    if args.admin:
        role_override = ROLE_ADMIN
    elif args.display_node:
        role_override = ROLE_DISPLAY_NODE

    app = CyberFishApp(
        config_path=Path(args.config),
        force_network_enabled=False if args.no_network else None,
        role_override=role_override,
        debug_net=args.debug_net,
    )
    app.run(max_seconds=args.duration)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
