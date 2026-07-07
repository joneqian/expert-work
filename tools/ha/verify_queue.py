"""Live distributed run-queue verification — Stream 9.5 (★2→★5 hard gate).

Proves the distributed run queue end to end against a **real** two-instance dev
stack where blue (``:8000``) only enqueues and green (``:8002``) drains, both
control-plane colours sharing one Postgres. CI can't do this: it needs two live
processes, a real model key (resolved server-side), and a genuine cross-instance
hand-off — none of which the deterministic suite has (its tests monkeypatch
``run_agent`` and run a single in-memory store).

What it does:

1. Create a session on **blue** and submit a run with ``mode=queue`` — blue
   returns ``202`` and persists a ``status='queued'`` row owned by no process
   (no ``claimed_by``). Blue's own queue worker is disabled by the
   ``queue-e2e`` overlay, so blue will never execute it.
2. Assert the run is durably ``queued`` with no owner (it really is sitting in
   the distributed queue, not mid-execution on blue).
3. ``docker kill`` **blue** — the instance that accepted the request is gone.
   The queued row is durable and unowned, so this proves the work survives the
   submitter's death.
4. **Green**'s :class:`RunQueueWorker` scans the queue, CAS-claims the run
   (``status='queued'`` → ``running`` + ownership lease → exactly-one winner),
   rebuilds the agent (cache hit), and executes it from the persisted
   ``enqueued_input``.
5. Assert the run reaches ``success`` owned by **green**, that green's
   ``expert_work_run_queue_dequeued_total`` metric incremented, and that exactly one
   instance ever claimed it (``claimed_by`` set once, no double execution).

Lease fields (``claimed_by``) aren't on the run API, so they are read straight
from Postgres via ``docker exec ... psql`` (the dev superuser bypasses RLS). The
API token is read from ``EXPERT_WORK_API_TOKEN`` and never logged.

Usage (bring the full dev stack up first — ``make dev-up`` — then the overlay)::

    docker compose -f infra/docker-compose.yml -f infra/docker-compose.queue-e2e.yml \
        --profile full up -d --no-deps control-plane-blue control-plane-green
    export EXPERT_WORK_API_TOKEN=<a dev-login bearer token>
    uv run python tools/ha/verify_queue.py
    uv run python tools/ha/verify_queue.py --agent my-agent@1.0.0

Exit code is non-zero when the distributed drain did not complete cleanly — so
it can gate a manual release check.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
from typing import Any

import httpx

#: OpenAI-compatible regional vendors we prefer to target when auto-picking.
_DOMESTIC_PROVIDERS = frozenset({"qwen", "glm", "deepseek", "doubao", "kimi", "self-hosted"})

#: A normal prompt — the queue path runs the agent to completion on green; no
#: kill window to hit, so it need not be long.
_PROMPT = "In two or three sentences, explain what a write-ahead log is and why databases use one."


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is not set — export it before running (see module docstring)")
    return value


def _unwrap(data: dict[str, Any]) -> dict[str, Any]:
    """Unwrap the ``{success, data, error}`` envelope used by agents/sessions."""
    if data.get("success") is False:
        err = data.get("error") or {}
        raise SystemExit(f"API error: {err.get('code')}: {err.get('message')}")
    inner = data.get("data")
    return inner if isinstance(inner, dict) else data


def _psql(pg_container: str, sql: str) -> str:
    """Run a one-shot read-only query as the dev superuser (bypasses RLS)."""
    db_user = os.environ.get("EXPERT_WORK_DB_USER", "expert_work")
    db_name = os.environ.get("EXPERT_WORK_DB_NAME", "expert_work_dev")
    proc = subprocess.run(
        ["docker", "exec", pg_container, "psql", "-U", db_user, "-d", db_name, "-tAc", sql],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _docker_kill(container: str) -> None:
    subprocess.run(["docker", "kill", container], capture_output=True, text=True, check=True)


async def _pick_agent(client: httpx.AsyncClient, override: str | None) -> tuple[str, str, str]:
    resp = await client.get("/v1/agents", params={"status": "active", "limit": 200})
    resp.raise_for_status()
    items = _unwrap(resp.json()).get("items", [])
    if not items:
        raise SystemExit("no active agents found on this stack")

    def provider_of(rec: dict[str, Any]) -> str:
        return str(rec.get("spec", {}).get("model", {}).get("provider", ""))

    if override is not None:
        name, _, version = override.partition("@")
        for rec in items:
            if rec.get("name") == name and (not version or rec.get("version") == version):
                return rec["name"], rec["version"], provider_of(rec)
        raise SystemExit(f"agent {override!r} not found among active agents")
    for rec in items:
        if provider_of(rec) in _DOMESTIC_PROVIDERS:
            return rec["name"], rec["version"], provider_of(rec)
    rec = items[0]
    return rec["name"], rec["version"], provider_of(rec)


async def _create_session(client: httpx.AsyncClient, name: str, version: str) -> str:
    resp = await client.post("/v1/sessions", json={"agent_name": name, "agent_version": version})
    resp.raise_for_status()
    return str(_unwrap(resp.json())["thread_id"])


async def _enqueue_run(client: httpx.AsyncClient, thread_id: str) -> str:
    """Submit a ``mode=queue`` run; assert the 202 + return the run_id."""
    resp = await client.post(
        f"/v1/sessions/{thread_id}/runs", json={"input": _PROMPT, "mode": "queue"}
    )
    if resp.status_code != 202:
        raise SystemExit(
            f"expected 202 from mode=queue submit, got {resp.status_code}: {resp.text}"
        )
    body = resp.json()
    if body.get("status") != "queued":
        raise SystemExit(f"expected status=queued in 202 body, got {body!r}")
    return str(body["run_id"])


def _run_row(pg_container: str, run_id: str) -> dict[str, str] | None:
    """The agent_run row by id: status / claimed_by / reclaim_count."""
    out = _psql(
        pg_container,
        "SELECT status || '|' || coalesce(claimed_by, '') || '|' || reclaim_count "
        f"FROM agent_run WHERE id = '{run_id}' LIMIT 1",
    )
    if not out:
        return None
    status, claimed_by, reclaim_count = out.split("|", 2)
    return {"status": status, "claimed_by": claimed_by, "reclaim_count": reclaim_count}


async def _await_queued(pg_container: str, run_id: str, timeout_s: float) -> dict[str, str]:
    """Poll until the run is durably ``queued`` with no owner."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        row = _run_row(pg_container, run_id)
        if row and row["status"] == "queued":
            return row
        await asyncio.sleep(0.3)
    raise SystemExit("timed out waiting for the run to reach durable queued state on blue")


async def _await_terminal(pg_container: str, run_id: str, timeout_s: float) -> dict[str, str]:
    """Poll until green drains the queued run to a terminal status."""
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        row = _run_row(pg_container, run_id)
        if row:
            tag = f"{row['status']} owner={row['claimed_by'][:24]}"
            if tag != last:
                print(f"  … {tag}")
                last = tag
            if row["status"] in ("success", "error", "timeout", "interrupted"):
                return row
        await asyncio.sleep(2.0)
    raise SystemExit("timed out waiting for green's queue worker to drain the run")


def _metric_dequeued(green_container: str) -> float:
    """Read green's dequeued counter from inside the container.

    We scrape ``/metrics`` over the container loopback via ``docker exec`` (the
    same approach as the 9.4 failover check) so this works whether or not green
    has a host port mapped.
    """
    code = (
        "import urllib.request;"
        "print(urllib.request.urlopen('http://127.0.0.1:8000/metrics', timeout=5).read().decode())"
    )
    try:
        out = subprocess.run(
            ["docker", "exec", green_container, "python", "-c", code],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except subprocess.CalledProcessError:
        return 0.0
    for line in out.splitlines():
        if line.startswith("expert_work_run_queue_dequeued_total"):
            return float(line.rsplit(" ", 1)[-1])
    return 0.0


async def _amain(args: argparse.Namespace) -> int:
    token = _require_env("EXPERT_WORK_API_TOKEN")  # never logged
    headers = {"Authorization": f"Bearer {token}"}

    # --- enqueue on blue ------------------------------------------------
    async with httpx.AsyncClient(base_url=args.blue_url, headers=headers, timeout=30.0) as blue:
        name, version, provider = await _pick_agent(blue, args.agent)
        print(f"agent: {name}@{version}  provider={provider or '?'}")
        thread_id = await _create_session(blue, name, version)
        print(f"session: {thread_id}")

        metric_before = _metric_dequeued(args.green_container)

        run_id = await _enqueue_run(blue, thread_id)
        print(f"run {run_id} submitted with mode=queue (blue returned 202)")

    queued = await _await_queued(args.pg_container, run_id, timeout_s=30.0)
    if queued["claimed_by"]:
        raise SystemExit(
            f"queued run already owned by {queued['claimed_by']} — blue worker not off?"
        )
    print("run is durably QUEUED with no owner — sitting in the distributed queue")

    # --- crash blue, the instance that accepted the request -------------
    print(f"killing {args.blue_container} (the submitter) …")
    _docker_kill(args.blue_container)

    # --- green's queue worker must claim + execute to completion --------
    print("waiting for green's run-queue worker to claim + drain …")
    terminal = await _await_terminal(args.pg_container, run_id, timeout_s=args.timeout)

    owner = terminal["claimed_by"]
    metric_after = _metric_dequeued(args.green_container)

    # --- verdict --------------------------------------------------------
    checks = {
        "status == success": terminal["status"] == "success",
        "claimed by an instance (green)": bool(owner),
        "reclaim_count == 0 (queue claim, not failover)": terminal["reclaim_count"] == "0",
        "green dequeued metric incremented": metric_after > metric_before,
    }
    print("\n--- distributed-queue verdict ---")
    print(f"final status      : {terminal['status']}")
    print(f"claimed_by (green): {owner[:36]}")
    print(f"reclaim_count     : {terminal['reclaim_count']}")
    print(f"dequeued metric   : {metric_before} → {metric_after}")
    print()
    for label, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

    all_ok = all(checks.values())
    print("\nRESULT:", "PASS — distributed queue drain verified." if all_ok else "FAIL.")
    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Live distributed run-queue verification (Stream 9.5)."
    )
    parser.add_argument(
        "--blue-url", default="http://localhost:8000", help="blue control-plane URL (enqueues)"
    )
    parser.add_argument("--agent", default=None, help="target agent as name@version (else auto)")
    parser.add_argument(
        "--blue-container", default="expert-work-control-plane-blue", help="blue container to kill"
    )
    parser.add_argument(
        "--green-container",
        default="expert-work-control-plane-green",
        help="green container (drains the queue; metrics scraped via docker exec)",
    )
    parser.add_argument("--pg-container", default="expert-work-postgres", help="Postgres container")
    parser.add_argument(
        "--timeout", type=float, default=180.0, help="seconds to wait for green to drain the run"
    )
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
