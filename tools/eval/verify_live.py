"""Live adversarial red-team verification — P1-S2.3 follow-up (11.5 ★4→★5).

Drives a **real** agent (real domestic model, key resolved server-side) over
the adversarial dataset through the running control-plane HTTP API, then
runs the deterministic safety judge on each reply. This is the live half
the CI suite can't do (no model key in CI); it closes the "module tested
but never run against a real agent" gap.

Keyless by construction: the model key lives in the server's DB and is
resolved server-side; this script only sends prompts + reads replies. The
API token is read from ``EXPERT_WORK_API_TOKEN`` and never logged.

Usage (bring the dev stack up first — ``make dev-up``)::

    export EXPERT_WORK_API_URL=http://localhost:8080     # your control-plane URL
    export EXPERT_WORK_API_TOKEN=<a dev-login bearer token>
    uv run python tools/eval/verify_live.py            # auto-pick a domestic agent
    uv run python tools/eval/verify_live.py --agent my-agent@1.0.0

Exit code is non-zero when any case is unsafe (a leak or an unrefused
jailbreak) — so it can gate a manual release check.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from expert_work.common.output_screen import REFUSAL_TEXT

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from adversarial import (  # type: ignore[import-not-found]  # noqa: E402
    AdversarialCase,
    load_cases,
    safety_verdict,
)

#: OpenAI-compatible regional vendors (ModelSpec.provider) we prefer to
#: target when auto-picking — these are the "domestic model" deployments.
_DOMESTIC_PROVIDERS = frozenset({"qwen", "glm", "deepseek", "doubao", "kimi", "self-hosted"})

_DEFAULT_DATASET = _EVAL_DIR / "datasets" / "adversarial" / "m0_baseline.yaml"


class _Observed:
    """Lightweight call-chain observation from the SSE stream (trace signal)."""

    def __init__(self) -> None:
        self.assistant_turns = 0
        self.tool_messages = 0
        # PI-3b — set when a tool result is the action-screen denial marker.
        self.action_blocked = False


def _iter_messages(obj: Any) -> list[dict[str, Any]]:
    """Recursively collect message-shaped dicts from an ``updates`` payload."""
    found: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        msgs = obj.get("messages")
        if isinstance(msgs, list):
            found.extend(m for m in msgs if isinstance(m, dict))
        for v in obj.values():
            found.extend(_iter_messages(v))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(_iter_messages(v))
    return found


def _message_type(msg: dict[str, Any]) -> str:
    return str(msg.get("type") or msg.get("role") or "")


def _content_text(msg: dict[str, Any]) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # content-block form: concatenate text blocks
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        msg = f"{name} is not set — export it before running (see module docstring)"
        raise SystemExit(msg)
    return value


def _unwrap(data: dict[str, Any]) -> dict[str, Any]:
    """Unwrap the ``{success, data, error}`` envelope used by agents/sessions."""
    if data.get("success") is False:
        err = data.get("error") or {}
        raise SystemExit(f"API error: {err.get('code')}: {err.get('message')}")
    inner = data.get("data")
    return inner if isinstance(inner, dict) else data


async def _pick_agent(client: httpx.AsyncClient, override: str | None) -> tuple[str, str, str]:
    """Return ``(name, version, provider)`` — override ``name@version`` or auto-pick."""
    resp = await client.get("/v1/agents", params={"status": "active", "limit": 200})
    resp.raise_for_status()
    items = _unwrap(resp.json()).get("items", [])
    if not items:
        raise SystemExit("no active agents found on this stack")

    def provider_of(rec: dict[str, Any]) -> str:
        spec = rec.get("spec", {})
        return str(spec.get("model", {}).get("provider", ""))

    if override is not None:
        name, _, version = override.partition("@")
        for rec in items:
            if rec.get("name") == name and (not version or rec.get("version") == version):
                return rec["name"], rec["version"], provider_of(rec)
        raise SystemExit(f"agent {override!r} not found among active agents")

    # Prefer a domestic-provider agent; fall back to the first active one.
    for rec in items:
        if provider_of(rec) in _DOMESTIC_PROVIDERS:
            return rec["name"], rec["version"], provider_of(rec)
    rec = items[0]
    return rec["name"], rec["version"], provider_of(rec)


async def _create_session(client: httpx.AsyncClient, name: str, version: str) -> str:
    resp = await client.post("/v1/sessions", json={"agent_name": name, "agent_version": version})
    resp.raise_for_status()
    return str(_unwrap(resp.json())["thread_id"])


async def _run_once(
    client: httpx.AsyncClient, thread_id: str, prompt: str
) -> tuple[str, _Observed]:
    """POST a run, stream the SSE, return ``(final_assistant_text, observed)``."""
    observed = _Observed()
    final_text = ""
    event = ""
    async with client.stream(
        "POST", f"/v1/sessions/{thread_id}/runs", json={"input": prompt}
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: ") and event == "updates":
                payload = json.loads(line[len("data: ") :])
                for msg in _iter_messages(payload):
                    mtype = _message_type(msg)
                    if mtype in ("ai", "AIMessageChunk", "AIMessage", "assistant"):
                        text = _content_text(msg)
                        if text.strip():
                            observed.assistant_turns += 1
                            final_text = text
                    elif mtype in ("tool", "ToolMessage"):
                        observed.tool_messages += 1
                        if "action screening" in _content_text(msg).lower():
                            observed.action_blocked = True
    return final_text, observed


async def run_verification(
    client: httpx.AsyncClient,
    cases: list[AdversarialCase],
    *,
    agent_override: str | None = None,
) -> int:
    """Drive every case through a real agent + judge. Returns the unsafe count.

    Injectable ``client`` so this is testable with an ``httpx.MockTransport``
    (no live stack). Prints a per-case red-team report.
    """
    name, version, provider = await _pick_agent(client, agent_override)
    print(f"agent: {name}@{version}  provider={provider or '?'}")
    thread_id = await _create_session(client, name, version)
    print(f"session: {thread_id}\n")

    unsafe = 0
    for case in cases:
        reply, obs = await _run_once(client, thread_id, case.prompt)
        safe, violations = safety_verdict(reply, case)
        # PI-2 — when the server's output screen blocked a leak it returns the
        # fixed refusal; surface that so a SAFE attributable to screening is
        # distinguishable from the model refusing on its own.
        screen_blocked = safe and reply.strip() == REFUSAL_TEXT
        if not safe:
            mark = "UNSAFE"
            unsafe += 1
        elif obs.action_blocked:
            # PI-3b — the action judge denied an injected tool call before it ran.
            mark = "SAFE action-blocked"
        elif screen_blocked:
            mark = "SAFE screen-blocked"
        else:
            mark = "SAFE"
        print(
            f"[{mark}] {case.case_id} ({case.adversarial_type}) "
            f"turns={obs.assistant_turns} tools={obs.tool_messages}"
        )
        for v in violations:
            print(f"        ! {v}")

    total = len(cases)
    safe_rate = (total - unsafe) / total if total else 0.0
    print(f"\nsafe_rate = {total - unsafe}/{total} = {safe_rate:.2f}")
    print(
        "RESULT: FAIL — at least one unsafe response (hard gate)."
        if unsafe
        else "RESULT: PASS — all cases safe."
    )
    return unsafe


async def _amain(args: argparse.Namespace) -> int:
    base_url = args.base_url or _require_env("EXPERT_WORK_API_URL")
    token = _require_env("EXPERT_WORK_API_TOKEN")  # never logged
    cases = load_cases(Path(args.dataset))
    if args.limit:
        cases = cases[: args.limit]

    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=120.0) as client:
        unsafe = await run_verification(client, cases, agent_override=args.agent)
    return 1 if unsafe else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live adversarial red-team verification.")
    parser.add_argument(
        "--base-url", default=None, help="control-plane URL (or $EXPERT_WORK_API_URL)"
    )
    parser.add_argument("--agent", default=None, help="target agent as name@version (else auto)")
    parser.add_argument("--dataset", default=str(_DEFAULT_DATASET), help="adversarial dataset path")
    parser.add_argument("--limit", type=int, default=0, help="cap number of cases (0 = all)")
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
