"""Live HA-failover verification — Stream 9.4 (★2→★5 hard gate).

Proves the orphaned-run failover end to end against a **real** two-instance
dev stack (blue ``:8000`` + green ``:8001``, both control-plane colours sharing
one Postgres). CI can't do this: it needs two live processes, a real model
key (resolved server-side), and an abrupt ``docker kill`` mid-run — none of
which the deterministic suite has.

What it does:

1. Start a long-running agent run on **blue** (real domestic model, key
   resolved server-side; this script only sends a prompt).
2. Wait until the run is durably ``running`` and **blue** owns its ownership
   lease (``claimed_by`` set, ``heartbeat_at`` touched).
3. ``docker kill`` **blue** — an abrupt crash, no graceful shutdown, so blue
   writes no terminal status. The run is now an orphan: ``status=running`` with
   a lease nobody is renewing.
4. **Green**'s :class:`OrphanSweep` detects the expired lease, reclaims the run
   (reclaim CAS → exactly-one winner), adopts it, and resumes it from its
   durable LangGraph checkpoint via ``run_agent(graph_input=None)``.
5. Assert the run reaches ``success`` under a *different* owner with
   ``reclaim_count >= 1``, a ``run:failover`` audit row exists, and green's
   ``helix_run_orphan_reclaimed_total`` metric incremented.

Lease fields (``claimed_by`` / ``reclaim_count``) aren't on the run API, so they
are read straight from Postgres via ``docker exec ... psql`` (the dev superuser
bypasses RLS). The API token is read from ``HELIX_API_TOKEN`` and never logged.

Usage (bring the full dev stack up first — ``make dev-up``; needs blue+green)::

    export HELIX_API_TOKEN=<a dev-login bearer token>
    uv run python tools/ha/verify_failover.py
    uv run python tools/ha/verify_failover.py --agent my-agent@1.0.0

Exit code is non-zero when failover did not complete cleanly — so it can gate
a manual release check.
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

#: A prompt long enough that the run is still executing when we kill blue —
#: the kill window only needs a few seconds but a long generation is safe.
_LONG_PROMPT = (
    "Think step by step and write a thorough, detailed technical explanation "
    "(at least 1500 words) of how distributed consensus protocols like Raft "
    "and Paxos achieve agreement despite node failures. Cover leader election, "
    "log replication, safety, and liveness. Be exhaustive and precise."
)


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
    db_user = os.environ.get("HELIX_DB_USER", "helix_agent")
    db_name = os.environ.get("HELIX_DB_NAME", "helix_agent_dev")
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


async def _start_run_bg(base_url: str, token: str, thread_id: str) -> None:
    """Open the run SSE stream on blue and consume until the stream dies.

    Fire-and-forget: when blue is killed mid-run the stream errors — that is
    the expected end of this task, so every transport error is swallowed.
    """
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with (
            httpx.AsyncClient(base_url=base_url, headers=headers, timeout=None) as client,
            client.stream(
                "POST", f"/v1/sessions/{thread_id}/runs", json={"input": _LONG_PROMPT}
            ) as resp,
        ):
            async for _line in resp.aiter_lines():
                pass
    except Exception:
        pass


def _run_row(pg_container: str, thread_id: str) -> dict[str, str] | None:
    """Latest agent_run row for the thread: status / claimed_by / reclaim_count."""
    out = _psql(
        pg_container,
        "SELECT id || '|' || status || '|' || coalesce(claimed_by, '') || '|' "
        "|| reclaim_count || '|' || coalesce(heartbeat_at::text, '') "
        f"FROM agent_run WHERE thread_id = '{thread_id}' ORDER BY created_at DESC LIMIT 1",
    )
    if not out:
        return None
    run_id, status, claimed_by, reclaim_count, heartbeat_at = out.split("|", 4)
    return {
        "run_id": run_id,
        "status": status,
        "claimed_by": claimed_by,
        "reclaim_count": reclaim_count,
        "heartbeat_at": heartbeat_at,
    }


async def _await_running(pg_container: str, thread_id: str, timeout_s: float) -> dict[str, str]:
    """Poll until the run is durably ``running`` and an instance owns its lease."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        row = _run_row(pg_container, thread_id)
        if row and row["status"] == "running" and row["claimed_by"] and row["heartbeat_at"]:
            return row
        await asyncio.sleep(0.5)
    raise SystemExit("timed out waiting for the run to reach running+claimed on blue")


async def _await_terminal(pg_container: str, thread_id: str, timeout_s: float) -> dict[str, str]:
    """Poll until the run leaves ``running`` (failover resolved it)."""
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        row = _run_row(pg_container, thread_id)
        if row:
            tag = f"{row['status']} owner={row['claimed_by'][:24]} reclaim={row['reclaim_count']}"
            if tag != last:
                print(f"  … {tag}")
                last = tag
            if row["status"] in ("success", "error", "timeout", "interrupted"):
                return row
        await asyncio.sleep(2.0)
    raise SystemExit("timed out waiting for failover to resolve the orphaned run")


def _audit_failover_count(pg_container: str, run_id: str) -> tuple[int, str]:
    out = _psql(
        pg_container,
        "SELECT count(*) || '|' || coalesce(max(result), '') FROM audit_log "
        f"WHERE action = 'run:failover' AND resource_id = '{run_id}'",
    )
    count, result = out.split("|", 1)
    return int(count), result


async def _metric_reclaimed(green_url: str) -> float:
    async with httpx.AsyncClient(base_url=green_url, timeout=10.0) as client:
        resp = await client.get("/metrics")
        resp.raise_for_status()
        for line in resp.text.splitlines():
            if line.startswith("helix_run_orphan_reclaimed_total"):
                return float(line.rsplit(" ", 1)[-1])
    return 0.0


async def _amain(args: argparse.Namespace) -> int:
    token = _require_env("HELIX_API_TOKEN")  # never logged
    headers = {"Authorization": f"Bearer {token}"}

    # --- setup on blue --------------------------------------------------
    async with httpx.AsyncClient(base_url=args.blue_url, headers=headers, timeout=30.0) as blue:
        name, version, provider = await _pick_agent(blue, args.agent)
        print(f"agent: {name}@{version}  provider={provider or '?'}")
        thread_id = await _create_session(blue, name, version)
        print(f"session: {thread_id}")

    metric_before = await _metric_reclaimed(args.green_url)

    # --- start the run on blue, then wait for it to own the lease -------
    run_task = asyncio.create_task(_start_run_bg(args.blue_url, token, thread_id))
    running = await _await_running(args.pg_container, thread_id, timeout_s=60.0)
    run_id = running["run_id"]
    blue_owner = running["claimed_by"]
    print(f"run {run_id} RUNNING on blue (owner={blue_owner[:24]}…)")

    # --- crash blue mid-run --------------------------------------------
    print(f"killing {args.blue_container} …")
    _docker_kill(args.blue_container)

    # --- green's sweep must reclaim + resume to completion -------------
    print("waiting for green's orphan sweep to reclaim + resume …")
    terminal = await _await_terminal(args.pg_container, thread_id, timeout_s=args.timeout)
    run_task.cancel()

    new_owner = terminal["claimed_by"]
    reclaim_count = int(terminal["reclaim_count"])
    audit_count, audit_result = _audit_failover_count(args.pg_container, run_id)
    metric_after = await _metric_reclaimed(args.green_url)

    # --- verdict --------------------------------------------------------
    checks = {
        "status == success": terminal["status"] == "success",
        "owner changed (failover)": bool(new_owner) and new_owner != blue_owner,
        "reclaim_count >= 1": reclaim_count >= 1,
        "run:failover audit present": audit_count >= 1 and audit_result == "success",
        "reclaimed metric incremented": metric_after > metric_before,
    }
    print("\n--- failover verdict ---")
    print(f"final status      : {terminal['status']}")
    print(f"owner blue→green  : {blue_owner[:24]}… → {new_owner[:24]}…")
    print(f"reclaim_count     : {reclaim_count}")
    print(f"failover audits   : {audit_count} (result={audit_result})")
    print(f"reclaimed metric  : {metric_before} → {metric_after}")
    print()
    for label, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

    all_ok = all(checks.values())
    print("\nRESULT:", "PASS — failover hot-handoff verified." if all_ok else "FAIL.")
    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live HA-failover verification (Stream 9.4).")
    parser.add_argument(
        "--blue-url", default="http://localhost:8000", help="blue control-plane URL"
    )
    parser.add_argument(
        "--green-url", default="http://localhost:8001", help="green control-plane URL"
    )
    parser.add_argument("--agent", default=None, help="target agent as name@version (else auto)")
    parser.add_argument(
        "--blue-container", default="helix-control-plane-blue", help="blue container to kill"
    )
    parser.add_argument("--pg-container", default="helix-postgres", help="Postgres container")
    parser.add_argument(
        "--timeout", type=float, default=180.0, help="seconds to wait for failover to resolve"
    )
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
