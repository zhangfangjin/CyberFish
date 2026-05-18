from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class SmokeTests(unittest.TestCase):
    def test_headless_app_runs_briefly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = os.environ.copy()
            env["SDL_VIDEODRIVER"] = "dummy"
            env["SDL_AUDIODRIVER"] = "dummy"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "cyberfish",
                    "--headless-smoke",
                    "--duration",
                    "1.0",
                    "--no-network",
                    "--config",
                    str(Path(temp_dir) / "config.json"),
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                check=False,
                text=True,
                capture_output=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
