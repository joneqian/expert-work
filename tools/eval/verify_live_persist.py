"""Live verify — per-user workspace durability across sandbox reclaim (J.15).

Drives the sandbox-supervisor HTTP API directly (no auth — internal service, host
port 8001) to prove the file-restoration guarantee behind the
sandbox-image-consolidation work:

* POSITIVE — a user-scoped acquire mounts that user's named volume. Write a file,
  ``destroy`` the sandbox (simulates the idle reaper: ``docker rm -f`` the
  container, the named volume survives), re-acquire as the SAME user → the file
  is restored.
* NEGATIVE (control) — a no-user acquire gets an ephemeral tmpfs. Same
  write→destroy→re-acquire cycle → the file is GONE. Proves it is the volume, not
  some daemon-side cache, doing the restoring.

Prereq: the stack is up (`make -C infra dev-up`) so sandbox-supervisor (:8001) +
postgres are running, and `helix-sandbox:dev` is built (`make -C infra
build-sandbox`).

    uv run python tools/eval/verify_live_persist.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid

import httpx

BASE = "http://localhost:8001"
FILE = "/workspace/persist_check.txt"


async def _acquire(client: httpx.AsyncClient, *, tenant: str, user: str | None) -> str:
    body: dict[str, object] = {"tenant_id": tenant, "thread_id": str(uuid.uuid4())}
    if user is not None:
        body["user_id"] = user
    resp = await client.post(f"{BASE}/v1/sandboxes:acquire", json=body)
    resp.raise_for_status()
    return str(resp.json()["sandbox_id"])


async def _exec(client: httpx.AsyncClient, sandbox_id: str, code: str) -> str:
    resp = await client.post(
        f"{BASE}/v1/sandboxes/{sandbox_id}:exec", json={"code": code, "timeout_s": 30}
    )
    resp.raise_for_status()
    out = resp.json()
    if out["exit_code"] != 0:
        raise RuntimeError(f"exec failed rc={out['exit_code']} stderr={out['stderr']!r}")
    return str(out["stdout"]).strip()


async def _destroy(client: httpx.AsyncClient, sandbox_id: str, reason: str) -> None:
    resp = await client.post(f"{BASE}/v1/sandboxes/{sandbox_id}:destroy", json={"reason": reason})
    resp.raise_for_status()


_WRITE = "p = {path!r}\nopen(p, 'w').write({marker!r})\nprint('wrote')"
_READ = "import os\np = {path!r}\nprint(open(p).read() if os.path.exists(p) else 'MISSING')"


async def _cycle(client: httpx.AsyncClient, *, user: str | None) -> tuple[str, str]:
    """write → destroy(reclaim) → re-acquire → read. Returns ``(read_back, marker)``."""
    tenant = str(uuid.uuid4())
    marker = f"persist-{uuid.uuid4().hex}"
    sbx1 = await _acquire(client, tenant=tenant, user=user)
    await _exec(client, sbx1, _WRITE.format(path=FILE, marker=marker))
    await _destroy(client, sbx1, reason="reclaim-sim")
    sbx2 = await _acquire(client, tenant=tenant, user=user)
    try:
        read_back = await _exec(client, sbx2, _READ.format(path=FILE))
        return read_back, marker
    finally:
        await _destroy(client, sbx2, reason="verify-cleanup")


async def main() -> int:
    async with httpx.AsyncClient(timeout=120.0) as client:
        health = await client.get(f"{BASE}/v1/health")
        if health.status_code != 200:
            print(f"supervisor not healthy: {health.status_code} {health.text}", file=sys.stderr)
            return 2

        user = str(uuid.uuid4())
        pos_read, pos_marker = await _cycle(client, user=user)
        neg_read, _ = await _cycle(client, user=None)

        ok_pos = pos_read == pos_marker
        ok_neg = neg_read == "MISSING"
        print(f"POSITIVE (user-scoped): restored={ok_pos} read={pos_read!r}")
        print(f"NEGATIVE (ephemeral):   gone={ok_neg} read={neg_read!r}")
        if ok_pos and ok_neg:
            print("\n✓ PERSIST LIVE OK — user files survive reclaim; ephemeral does not")
            return 0
        print("\n✗ PERSIST LIVE FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
