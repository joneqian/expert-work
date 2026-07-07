"""Live verification for production quality monitoring — Stream RT-5 (RT-ADR-22~26).

Closes the "code green != it actually samples, judges, and alerts" gap for the
RT-5 chain (PRs #937/#938/#939) against a running dev stack. Two phases:

* **Phase 1 — real sampling -> judge -> persist -> aux metering (pure API).**
  Registers the ``quality-test`` agent and drives it with a handful of ordinary
  questions. With sampling set to 100%, the ``QualityMonitorWorker`` samples
  each finished run, the ``QualityJudge`` (RT-1 structured output) scores it, and
  the verdict lands in ``quality_score``. Asserts the scores show up via
  ``GET /v1/quality/scores`` AND that a ``quality_sampling`` aux-usage kind is
  visible via ``GET /v1/usage/tokens``.

* **Phase 2 — injected degradation -> drift -> IM alert.**
  Drift is a *temporal* signal: a recent window's mean must drop below an older
  baseline. There is no way to create aged history through the API, so this
  phase seeds the ``quality_score`` series directly in Postgres (needs
  ``EXPERT_WORK_DB_DSN``): a dense baseline of high scores backdated into the baseline
  window + a dense recent window of low scores, for a synthetic agent. It then
  registers a ``quality.drift`` webhook (pointed at your IM bot) and waits for
  the ``QualityDriftWorker`` to detect the drop, raise a ``quality_drift_alert``,
  and emit the webhook. Asserts the alert shows up via
  ``GET /v1/quality/drift-alerts``; the IM delivery itself is confirmed by eye on
  your bot (there is no delivery read API).

  Honest boundary: the baseline/recent split is DB-seeded, not driven as live
  conversation — backdating scores by days is impossible through the running
  system. The RT-5 code under test (window stats -> drift detection -> alert
  persist -> off-spine webhook emit) is exercised for real; only the aged input
  series is synthetic. Phase 1 covers the real-traffic sample+judge path.

Keyless: the model key is resolved server-side; this script only sends prompts.
The API token (a **system_admin** dev-login bearer) is read from
``EXPERT_WORK_API_TOKEN`` and never logged.

Prereqs — bring up the dev stack with quality monitoring ON and FAST::

    # In infra/.env (or the control-plane environment), before ``make dev-up``:
    EXPERT_WORK_ENABLE_QUALITY_MONITOR=true
    EXPERT_WORK_QUALITY_SAMPLING_RATE_PCT=100      # sample every finished run
    EXPERT_WORK_QUALITY_MONITOR_INTERVAL_S=15      # sample promptly
    EXPERT_WORK_QUALITY_DRIFT_INTERVAL_S=20        # detect drift promptly
    EXPERT_WORK_QUALITY_DRIFT_MIN_SAMPLES=10       # matches the seed density

Then::

    export EXPERT_WORK_API_URL=http://localhost:8000     # control-plane (8080 is Keycloak)
    export EXPERT_WORK_API_TOKEN=<system_admin dev bearer token>
    export EXPERT_WORK_DB_DSN=postgresql://expert_work:expert_work@localhost:5432/expert_work
    export EXPERT_WORK_IM_WEBHOOK_URL=<your feishu/dingtalk/wecom bot webhook>  # optional
    export EXPERT_WORK_IM_PAYLOAD_FORMAT=feishu           # generic|feishu|dingtalk|wecom
    # EDIT manifests/quality-test/v1.0.0.yaml model block to your provider first.
    uv run python tools/eval/verify_live_quality.py

Exit code is non-zero when any phase fails — so it can gate a manual release
check. ``--phase1-only`` / ``--phase2-only`` run a single phase.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is not set — export it before running (see module docstring)")
    return value


def _unwrap(data: dict[str, Any]) -> dict[str, Any]:
    """Unwrap the ``{success, data, error}`` envelope (sessions / me / usage)."""
    if data.get("success") is False:
        err = data.get("error") or {}
        raise SystemExit(f"API error: {err.get('code')}: {err.get('message')}")
    inner = data.get("data")
    return inner if isinstance(inner, dict) else data


# ── agent + run plumbing (shared shape with verify_live_egress) ───────────────


async def register_agent(
    client: httpx.AsyncClient, *, manifest_path: str
) -> tuple[str, str] | None:
    """Register the agent from a manifest YAML; return ``(name, version)``.
    Idempotent: a 409 (already registered) is treated as success."""
    import yaml

    text = Path(manifest_path).read_text()
    meta = (yaml.safe_load(text) or {}).get("metadata", {})
    name, version = meta.get("name"), str(meta.get("version"))
    print(f"[setup] register agent {name}@{version} from {manifest_path}")
    resp = await client.post("/v1/agents", json={"manifest_yaml": text})
    if resp.status_code == 409:
        print("  already registered — reusing")
        return name, version
    if resp.status_code not in (200, 201):
        print(f"  FAIL — register HTTP {resp.status_code}: {resp.text[:300]}")
        return None
    print("  registered")
    return name, version


async def _create_session(client: httpx.AsyncClient, name: str, version: str) -> str:
    resp = await client.post("/v1/sessions", json={"agent_name": name, "agent_version": version})
    resp.raise_for_status()
    return str(_unwrap(resp.json())["thread_id"])


async def _run_to_completion(client: httpx.AsyncClient, thread_id: str, prompt: str) -> str | None:
    """Drive one run to completion over SSE; return the run_id (or None)."""
    async with client.stream(
        "POST", f"/v1/sessions/{thread_id}/runs", json={"input": prompt}
    ) as resp:
        resp.raise_for_status()
        run_id = resp.headers.get("X-Expert-Work-Run-Id")
        # Drain the stream so the run finishes server-side (status -> success).
        async for _line in resp.aiter_lines():
            pass
    return run_id


# ── Phase 1: real sampling -> judge -> persist -> aux metering ────────────────

_PROMPTS = [
    "What is the capital of France? Answer in one sentence.",
    "What is 12 multiplied by 8?",
    "Name one primary color.",
    "In one sentence, what does HTTP stand for?",
    "What is the boiling point of water at sea level in Celsius?",
    "Which planet is closest to the sun?",
]


async def phase_sampling(client: httpx.AsyncClient, *, manifest: str, min_scores: int) -> bool:
    print("\n[phase 1] real sampling -> judge -> persist -> aux metering")
    registered = await register_agent(client, manifest_path=manifest)
    if registered is None:
        return False
    name, version = registered

    print(f"  driving {len(_PROMPTS)} runs (sampling must be 100% for a deterministic score set)…")
    driven = 0
    for prompt in _PROMPTS:
        thread_id = await _create_session(client, name, version)
        run_id = await _run_to_completion(client, thread_id, prompt)
        if run_id:
            driven += 1
    print(f"  {driven} runs completed; waiting for the sampler to judge + persist…")

    scores = await _poll_scores(client, agent_name=name, want=min_scores)
    if len(scores) < min_scores:
        print(
            f"  FAIL — only {len(scores)} score(s) after polling "
            f"(want >= {min_scores}). Is ENABLE_QUALITY_MONITOR on + sampling 100%?"
        )
        return False
    sample = scores[0]
    print(
        f"  PASS — {len(scores)} scores persisted; latest overall={sample.get('overall')} "
        f"dims={sample.get('dimensions')} judge={sample.get('judge_model')}."
    )

    # Aux metering (soft): the sampler meters judge tokens under a dedicated kind.
    ok_usage = await _check_aux_metering(client)
    if not ok_usage:
        print(
            "  WARN — no 'quality_sampling' usage kind visible yet. Metering may "
            "aggregate lazily; not failing phase 1 on it. Re-check /settings/usage."
        )
    return True


async def _poll_scores(
    client: httpx.AsyncClient, *, agent_name: str, want: int, attempts: int = 30
) -> list[dict[str, Any]]:
    for _ in range(attempts):
        resp = await client.get(
            "/v1/quality/scores", params={"agent_name": agent_name, "window_h": 24, "limit": 50}
        )
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            if len(items) >= want:
                return items
        await asyncio.sleep(3)
    # Return whatever we have on timeout.
    resp = await client.get("/v1/quality/scores", params={"agent_name": agent_name, "limit": 50})
    return resp.json().get("items", []) if resp.status_code == 200 else []


async def _check_aux_metering(client: httpx.AsyncClient, attempts: int = 5) -> bool:
    for _ in range(attempts):
        resp = await client.get("/v1/usage/tokens")
        if resp.status_code == 200:
            data = _unwrap(resp.json())
            by_kind = data.get("by_kind") or []
            if any(row.get("key") == "quality_sampling" for row in by_kind):
                print("  aux metering OK — 'quality_sampling' usage kind is visible.")
                return True
        await asyncio.sleep(2)
    return False


# ── Phase 2: seed degradation -> drift detection -> IM alert ──────────────────

_DRIFT_AGENT = "quality-drift-probe"


def _normalize_dsn(dsn: str) -> str:
    """asyncpg wants a plain ``postgresql://`` DSN (no SQLAlchemy driver suffix)."""
    return dsn.replace("+asyncpg", "").replace("+psycopg", "").replace("+psycopg2", "")


async def _me_tenant(client: httpx.AsyncClient) -> UUID:
    resp = await client.get("/v1/me")
    resp.raise_for_status()
    return UUID(str(_unwrap(resp.json())["tenant_id"]))


async def _seed_series(dsn: str, tenant_id: UUID, *, count: int) -> None:
    """Seed a baseline of high scores + a recent window of low scores.

    Baseline lands in ``[now-7d, now-25h]`` (high=5), recent in
    ``[now-23h, now-5min]`` (low=2) — a 60% drop over the default 24h/168h
    windows, well past the 15% default threshold, with ``count`` samples each
    to clear ``min_samples``.
    """
    import asyncpg

    now = datetime.now(tz=UTC)
    rows: list[tuple[datetime, int]] = []
    for i in range(count):
        # Baseline: spread across the 7d..25h-ago band.
        base_at = now - timedelta(hours=25) - timedelta(hours=(143 * i) / max(count - 1, 1))
        rows.append((base_at, 5))
    for i in range(count):
        # Recent: spread across the 23h..5min-ago band.
        rec_at = now - timedelta(hours=23) + timedelta(hours=(23 * i) / max(count - 1, 1))
        rows.append((rec_at, 2))

    conn = await asyncpg.connect(_normalize_dsn(dsn))
    try:
        async with conn.transaction():
            # ENABLE-RLS table: set the tenant GUC so an app-role DSN passes the
            # policy (an owner/superuser DSN is exempt and ignores it).
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))
            for observed_at, overall in rows:
                await conn.execute(
                    """
                    INSERT INTO quality_score (
                        tenant_id, agent_name, agent_version, run_id, thread_id,
                        overall, dimensions, rationale, judge_model, observed_at
                    ) VALUES ($1, $2, '1', $3, $4, $5, $6::jsonb, $7, $8, $9)
                    ON CONFLICT (tenant_id, run_id) DO NOTHING
                    """,
                    tenant_id,
                    _DRIFT_AGENT,
                    uuid4(),
                    uuid4(),
                    overall,
                    json.dumps({"addressed_request": overall, "coherence": overall, "safety": 5}),
                    "seeded degradation" if overall <= 2 else "seeded baseline",
                    "verify-live-quality",
                    observed_at,
                )
    finally:
        await conn.close()
    print(f"  seeded {count} baseline (5) + {count} recent (2) scores for {_DRIFT_AGENT!r}.")


async def _register_drift_webhook(
    client: httpx.AsyncClient, *, url: str, payload_format: str
) -> str | None:
    resp = await client.post(
        "/v1/webhook-endpoints",
        json={
            "name": "rt5-drift-live",
            "url": url,
            "event_types": ["quality.drift"],
            "agent_name": _DRIFT_AGENT,
            "payload_format": payload_format,
        },
    )
    if resp.status_code not in (200, 201):
        print(
            f"  WARN — could not register drift webhook (HTTP {resp.status_code}); "
            "alert will still be asserted via the API."
        )
        return None
    endpoint_id = str(_unwrap(resp.json())["id"])
    print(f"  registered quality.drift webhook -> {payload_format} bot (endpoint {endpoint_id}).")
    return endpoint_id


async def _poll_drift_alert(
    client: httpx.AsyncClient, *, attempts: int = 30
) -> dict[str, Any] | None:
    for _ in range(attempts):
        resp = await client.get(
            "/v1/quality/drift-alerts", params={"agent_name": _DRIFT_AGENT, "limit": 10}
        )
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            if items:
                return items[0]
        await asyncio.sleep(3)
    return None


async def _cleanup(
    client: httpx.AsyncClient, dsn: str, tenant_id: UUID, endpoint_id: str | None
) -> None:
    if endpoint_id:
        try:
            await client.delete(f"/v1/webhook-endpoints/{endpoint_id}")
        except httpx.HTTPError:
            # Best-effort cleanup — a leftover test webhook is harmless.
            pass
    try:
        import asyncpg

        conn = await asyncpg.connect(_normalize_dsn(dsn))
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))
                await conn.execute(
                    "DELETE FROM quality_score WHERE tenant_id = $1 AND agent_name = $2",
                    tenant_id,
                    _DRIFT_AGENT,
                )
        finally:
            await conn.close()
    except Exception as exc:
        print(f"  (cleanup: could not remove seeded rows: {type(exc).__name__})")


async def phase_drift(client: httpx.AsyncClient, *, count: int) -> bool:
    print("\n[phase 2] injected degradation -> drift detection -> IM alert")
    dsn = _require_env("EXPERT_WORK_DB_DSN")
    im_url = os.environ.get(
        "EXPERT_WORK_IM_WEBHOOK_URL", "https://example.invalid/no-bot-configured"
    )
    payload_format = os.environ.get("EXPERT_WORK_IM_PAYLOAD_FORMAT", "generic")

    tenant_id = await _me_tenant(client)
    await _seed_series(dsn, tenant_id, count=count)
    endpoint_id = await _register_drift_webhook(client, url=im_url, payload_format=payload_format)

    print("  waiting for the drift worker to detect the drop + raise an alert…")
    alert = await _poll_drift_alert(client)
    if alert is not None:
        print(
            f"  PASS — drift alert raised: recent_mean={alert.get('recent_mean')} "
            f"baseline_mean={alert.get('baseline_mean')} "
            f"drop={round((alert.get('drift_pct') or 0) * 100, 1)}% "
            f"(n={alert.get('recent_count')}/{alert.get('baseline_count')})."
        )
        if im_url.endswith("no-bot-configured"):
            print(
                "  NOTE: no EXPERT_WORK_IM_WEBHOOK_URL set — set one + re-run to verify delivery."
            )
        else:
            print(f"  NOTE: confirm the '{payload_format}' bot received the drift message by eye.")
    else:
        print(
            "  FAIL — no drift alert after polling. Check ENABLE_QUALITY_MONITOR + "
            "QUALITY_DRIFT_INTERVAL_S is short + QUALITY_DRIFT_MIN_SAMPLES <= seed count."
        )
    await _cleanup(client, dsn, tenant_id, endpoint_id)
    return alert is not None


# ── main ──────────────────────────────────────────────────────────────────────

_DEFAULT_MANIFEST = (
    Path(__file__).resolve().parents[2] / "manifests" / "quality-test" / "v1.0.0.yaml"
)


async def _amain(args: argparse.Namespace) -> int:
    base_url = args.base_url or _require_env("EXPERT_WORK_API_URL")
    token = _require_env("EXPERT_WORK_API_TOKEN")  # never logged
    headers = {"Authorization": f"Bearer {token}"}

    ok = True
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=180.0) as client:
        if not args.phase2_only:
            ok = (
                await phase_sampling(client, manifest=args.manifest, min_scores=args.min_scores)
                and ok
            )
        if not args.phase1_only:
            ok = await phase_drift(client, count=args.seed_count) and ok

    print(f"\nRESULT: {'PASS — quality monitoring verified live.' if ok else 'FAIL — see above.'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live RT-5 quality-monitoring verification.")
    parser.add_argument(
        "--base-url", default=None, help="control-plane URL (or $EXPERT_WORK_API_URL)"
    )
    parser.add_argument(
        "--manifest", default=str(_DEFAULT_MANIFEST), help="quality-test agent manifest"
    )
    parser.add_argument(
        "--min-scores", type=int, default=3, help="phase 1: min persisted scores to pass"
    )
    parser.add_argument(
        "--seed-count", type=int, default=12, help="phase 2: baseline/recent samples each"
    )
    parser.add_argument("--phase1-only", action="store_true", help="run only the sampling phase")
    parser.add_argument("--phase2-only", action="store_true", help="run only the drift phase")
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
