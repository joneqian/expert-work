"""Blue/green deploy for the control-plane — Stream I.2.

STREAM-I-DESIGN § 6. The control-plane is stateless after the ADR B-6
SQL-store cutover, so two colours (``control-plane-blue`` /
``control-plane-green``) can run against the same database. This script
recreates the idle colour with a new image tag, gates on its readiness,
optionally steps traffic through a weighted canary, flips the nginx
upstream, and drains the old colour.

The old colour's container is *stopped but kept* — ``rollback.py``
(I.3) flips the nginx upstream back to it for a sub-second rollback.

Usage::

    python tools/deploy/deploy.py --tag v1.2.3
    python tools/deploy/deploy.py --tag v1.2.3 --canary 10,50 --canary-pause 60
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

#: Repo root — ``tools/deploy/deploy.py`` → ``parents[2]``.
_REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = _REPO_ROOT / "infra" / "docker-compose.yml"
UPSTREAM_CONF = _REPO_ROOT / "infra" / "nginx" / "conf.d" / "control-plane-upstream.conf"

COLORS: tuple[str, str] = ("blue", "green")

_HEADER = (
    "# Blue/green upstream for the control-plane (Stream I.2 / STREAM-I-DESIGN § 6).\n"
    "#\n"
    "# MANAGED FILE — rewritten by tools/deploy/deploy.py on every deploy /\n"
    "# rollback / canary step. Do not edit by hand; run deploy.py instead.\n"
)

_SERVER_RE = re.compile(r"server\s+control-plane-(blue|green):\d+(?:\s+weight=(\d+))?")


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested — no Docker)
# ---------------------------------------------------------------------------


def other_color(color: str) -> str:
    """Return the opposite colour."""
    if color not in COLORS:
        raise ValueError(f"unknown colour: {color!r}")
    return "green" if color == "blue" else "blue"


def parse_live_color(upstream_conf: str) -> str:
    """Return the colour currently serving (the majority of) traffic.

    A bare ``server`` line (no ``weight``) counts as the full/sole
    upstream; during a canary the higher-weight colour is "live".
    """
    entries: list[tuple[str, str]] = _SERVER_RE.findall(upstream_conf)
    if not entries:
        raise ValueError("no control-plane server line found in upstream conf")
    # weight '' (bare server line) → treat as 100 (full traffic).
    weighted = [(color, int(weight) if weight else 100) for color, weight in entries]
    weighted.sort(key=lambda cw: cw[1], reverse=True)
    return weighted[0][0]


def render_upstream(live: str, *, canary_to_idle: int | None = None) -> str:
    """Render the nginx upstream include file.

    :param live: the colour serving the bulk of traffic.
    :param canary_to_idle: percent of traffic to route to the *idle*
        colour. ``None`` → 100% to ``live`` (a stable, single-server
        state — the pre-deploy and post-flip shape).
    """
    if live not in COLORS:
        raise ValueError(f"unknown colour: {live!r}")
    if canary_to_idle is None:
        servers = f"    server control-plane-{live}:8000;\n"
    else:
        if not 0 < canary_to_idle < 100:
            raise ValueError("canary_to_idle must be in the open interval (0, 100)")
        idle = other_color(live)
        servers = (
            f"    server control-plane-{live}:8000 weight={100 - canary_to_idle};\n"
            f"    server control-plane-{idle}:8000 weight={canary_to_idle};\n"
        )
    return f"{_HEADER}upstream control_plane_upstream {{\n{servers}    keepalive 32;\n}}\n"


def parse_canary_steps(raw: str | None) -> list[int]:
    """Parse ``--canary`` (e.g. ``"10,50"``) into a list of percentages."""
    if not raw:
        return []
    steps = [int(part) for part in raw.split(",")]
    for pct in steps:
        if not 0 < pct < 100:
            raise ValueError(f"canary step out of range (0, 100): {pct}")
    return steps


# ---------------------------------------------------------------------------
# Docker / compose orchestration
# ---------------------------------------------------------------------------


def _run(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command — fixed argv, no shell (deploy tooling helper)."""
    return subprocess.run(  # noqa: S603 — fixed argv list, shell=False
        cmd,
        env=env,
        check=check,
        text=True,
        capture_output=capture,
    )


def _compose(
    *args: str,
    tag: str | None = None,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run ``docker compose`` against the infra compose file."""
    env = dict(os.environ)
    if tag is not None:
        env["HELIX_CONTROL_PLANE_TAG"] = tag
    return _run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), *args],
        env=env,
        check=check,
        capture=capture,
    )


def write_upstream(text: str) -> None:
    """Write the nginx upstream include file (the deploy/rollback hinge)."""
    UPSTREAM_CONF.write_text(text)


def reload_nginx() -> None:
    """Hot-reload nginx so it re-reads the rewritten upstream include."""
    _compose("exec", "-T", "nginx", "nginx", "-s", "reload")


def wait_ready(color: str, timeout_s: float) -> None:
    """Poll a colour's ``/healthz/ready`` until 200 or timeout.

    Readiness (A.11) covers the DB / Redis dependencies — a richer gate
    than the compose TCP healthcheck.
    """
    service = f"control-plane-{color}"
    probe = (
        "import urllib.request; "
        "urllib.request.urlopen('http://127.0.0.1:8000/healthz/ready', timeout=2)"
    )
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = _compose("exec", "-T", service, "python", "-c", probe, check=False, capture=True)
        if result.returncode == 0:
            return
        time.sleep(3)
    raise TimeoutError(f"{service} did not become ready within {timeout_s:.0f}s")


def deploy(
    *,
    tag: str,
    canary: list[int],
    canary_pause: float,
    drain_timeout: int,
    ready_timeout: float,
) -> None:
    """Run a blue/green deploy: recreate idle → gate → canary → flip → drain."""
    live = parse_live_color(UPSTREAM_CONF.read_text())
    idle = other_color(live)
    print(f"[deploy] live={live} idle={idle} tag={tag}")

    print(f"[deploy] recreating control-plane-{idle} on tag {tag}")
    _compose("up", "-d", "--no-deps", "--force-recreate", f"control-plane-{idle}", tag=tag)

    print(f"[deploy] waiting for control-plane-{idle} /healthz/ready")
    wait_ready(idle, ready_timeout)

    for pct in canary:
        write_upstream(render_upstream(live, canary_to_idle=pct))
        reload_nginx()
        print(f"[deploy] canary: {pct}% → {idle}; pausing {canary_pause:.0f}s")
        time.sleep(canary_pause)

    write_upstream(render_upstream(idle))
    reload_nginx()
    print(f"[deploy] flipped: 100% → {idle}")

    _compose("stop", "-t", str(drain_timeout), f"control-plane-{live}")
    print(f"[deploy] drained + stopped control-plane-{live} (kept for rollback)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Blue/green deploy for the control-plane (I.2).")
    parser.add_argument(
        "--tag",
        default="dev",
        help="control-plane image tag to deploy (HELIX_CONTROL_PLANE_TAG). Default: dev.",
    )
    parser.add_argument(
        "--canary",
        default=None,
        help="comma-separated traffic percentages to step through before the full "
        "flip, e.g. '10,50'. Omit for a straight cut-over.",
    )
    parser.add_argument(
        "--canary-pause",
        type=float,
        default=30.0,
        help="seconds to hold at each canary step (watch the Stream G SLO board).",
    )
    parser.add_argument(
        "--drain-timeout",
        type=int,
        default=30,
        help="seconds to let the old colour drain in-flight requests before SIGKILL.",
    )
    parser.add_argument(
        "--ready-timeout",
        type=float,
        default=120.0,
        help="seconds to wait for the new colour's /healthz/ready.",
    )
    args = parser.parse_args(argv)

    try:
        deploy(
            tag=args.tag,
            canary=parse_canary_steps(args.canary),
            canary_pause=args.canary_pause,
            drain_timeout=args.drain_timeout,
            ready_timeout=args.ready_timeout,
        )
    except (TimeoutError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"[deploy] FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
