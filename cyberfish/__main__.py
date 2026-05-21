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
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.headless_smoke:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

    from .app import CyberFishApp

    app = CyberFishApp(
        config_path=Path(args.config),
        force_network_enabled=False if args.no_network else None,
    )
    app.run(max_seconds=args.duration)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
