from __future__ import annotations

import argparse
import os
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cyberfish",
        description="CyberFish Pygame LAN aquarium",
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to the JSON config file. Defaults to ./config.json.",
    )
    parser.add_argument(
        "--headless-smoke",
        action="store_true",
        help="Run with SDL dummy drivers for automated smoke checks.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Optional maximum runtime in seconds.",
    )
    parser.add_argument(
        "--no-network",
        action="store_true",
        help="Disable UDP discovery and fish transfers for this run.",
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
