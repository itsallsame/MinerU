"""Run the offline Web's synthetic-API Chromium regressions as one suite."""

from __future__ import annotations

import functools
import http.server
import subprocess
import sys
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
TESTS = sorted((ROOT / "tests").glob("*_browser.py"))


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> int:
    if not (DIST / "asset-manifest.json").is_file():
        print("Build offline assets first: pnpm build", file=sys.stderr)
        return 2
    if not TESTS:
        print("No browser regression scripts found", file=sys.stderr)
        return 2
    handler = functools.partial(QuietHandler, directory=str(DIST))
    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        for test in TESTS:
            print(f"Running {test.name}", flush=True)
            result = subprocess.run([sys.executable, str(test), base_url], check=False)
            if result.returncode:
                print(f"Failed: {test.name} (exit {result.returncode})", file=sys.stderr)
                return result.returncode
        server.shutdown()
    print(f"Passed {len(TESTS)} offline Chromium browser regressions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
