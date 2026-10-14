"""Container healthcheck with self-restart.

Docker marks a container ``unhealthy`` but never restarts it. This probe counts consecutive
failures in a file and, after ``--max-failures``, sends SIGTERM to PID 1. The process exits
and the ``restart: unless-stopped`` policy brings the container back. No Docker socket is
needed (no autoheal sidecar).

    python -m app.healthcheck http http://localhost:8000/health/ready
    python -m app.healthcheck arq          # worker: arq health key in Redis
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import urllib.request
from pathlib import Path

STATE = Path(os.environ.get("HEALTHCHECK_STATE", "/tmp/healthcheck-failures"))  # noqa: S108


def probe_http(url: str, timeout: float) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 (fixed local URL)
            status: int = r.status
            return 200 <= status < 300
    except Exception:
        return False


def probe_arq(timeout: float) -> bool:
    try:
        r = subprocess.run(
            ["arq", "--check", "app.workers.settings.WorkerSettings"],  # noqa: S607
            timeout=timeout,
            capture_output=True,
        )
    except Exception:
        return False
    return r.returncode == 0


def record(ok: bool, max_failures: int, kill: bool = True) -> int:
    """Update the failure counter; returns the exit code for Docker (0 healthy, 1 not)."""
    if ok:
        STATE.unlink(missing_ok=True)
        return 0
    try:
        failures = int(STATE.read_text()) + 1
    except (OSError, ValueError):
        failures = 1
    STATE.write_text(str(failures))
    if failures >= max_failures and kill:
        print(f"healthcheck failed {failures} times in a row: restarting", file=sys.stderr)
        STATE.unlink(missing_ok=True)
        os.kill(1, signal.SIGTERM)
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["http", "arq"])
    ap.add_argument("url", nargs="?", default="http://localhost:8000/health/ready")
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--max-failures", type=int, default=3)
    ap.add_argument("--no-restart", action="store_true", help="only report, never kill PID 1")
    args = ap.parse_args(argv)
    ok = probe_http(args.url, args.timeout) if args.kind == "http" else probe_arq(args.timeout)
    return record(ok, args.max_failures, kill=not args.no_restart)


if __name__ == "__main__":
    raise SystemExit(main())
