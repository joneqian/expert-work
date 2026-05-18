"""One-command rollback for the control-plane — Stream I.3.

STREAM-I-DESIGN § 7. Two paths:

* **fast** (default) — the previous colour's container was stopped but
  *kept* by the last ``deploy.py`` run. Rollback restarts it, flips the
  nginx upstream back, and drains the current (bad) colour. Sub-second
  traffic switch — no image pull, no recreate.
* **--to-tag TAG** — the previous colour's container is gone (a later
  deploy recreated it) or an arbitrary older version is wanted. Runs a
  full blue/green deploy of ``TAG`` via ``deploy.py``.

DB compatibility — see ``docs/runbooks/deployment.md`` (expand-contract):
a release's migrations only ever move forward, so the previous
control-plane image always runs against the current schema, which is
what makes rolling the *code* back (without rolling the schema) safe.

Usage::

    python tools/deploy/rollback.py                  # fast path
    python tools/deploy/rollback.py --to-tag v1.2.2  # redeploy an old tag
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from deploy import (
    UPSTREAM_CONF,
    _compose,
    deploy,
    other_color,
    parse_live_color,
    reload_nginx,
    render_upstream,
    wait_ready,
    write_upstream,
)


def rollback_fast(*, drain_timeout: int, ready_timeout: float) -> None:
    """Restart the previous (kept) colour and flip the nginx upstream back."""
    bad = parse_live_color(UPSTREAM_CONF.read_text())
    target = other_color(bad)
    print(f"[rollback] current={bad} → rolling back to {target}")

    started = _compose("start", f"control-plane-{target}", check=False)
    if started.returncode != 0:
        raise RuntimeError(
            f"could not start control-plane-{target} — its container is gone. "
            f"Use the fallback path: rollback.py --to-tag <previous-tag>"
        )

    print(f"[rollback] waiting for control-plane-{target} /healthz/ready")
    wait_ready(target, ready_timeout)

    write_upstream(render_upstream(target))
    reload_nginx()
    print(f"[rollback] flipped: 100% → {target}")

    _compose("stop", "-t", str(drain_timeout), f"control-plane-{bad}")
    print(f"[rollback] drained + stopped control-plane-{bad}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Roll back the control-plane (I.3).")
    parser.add_argument(
        "--to-tag",
        default=None,
        help="fallback path — roll back by redeploying this image tag. Use when "
        "the previous colour's container is no longer around. Omit for the fast "
        "path (restart the kept previous colour).",
    )
    parser.add_argument(
        "--drain-timeout",
        type=int,
        default=30,
        help="seconds to let the bad colour drain in-flight requests before SIGKILL.",
    )
    parser.add_argument(
        "--ready-timeout",
        type=float,
        default=120.0,
        help="seconds to wait for the rolled-back colour's /healthz/ready.",
    )
    args = parser.parse_args(argv)

    try:
        if args.to_tag is not None:
            print(f"[rollback] fallback path — redeploying tag {args.to_tag}")
            deploy(
                tag=args.to_tag,
                canary=[],
                canary_pause=0.0,
                drain_timeout=args.drain_timeout,
                ready_timeout=args.ready_timeout,
            )
        else:
            rollback_fast(drain_timeout=args.drain_timeout, ready_timeout=args.ready_timeout)
    except (TimeoutError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[rollback] FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
