"""Live verification for skill runtime auto-mount + import classification.

Closes the "code green ≠ it actually runs" gap for skill-runtime §5.1/§5.2
(PRs #735-#738). Two phases against a running control-plane:

* **Phase 1 — import + classify (§5.2).** Import a skill from a public GitHub
  repo via the platform endpoint and assert the response carries a ``runtime``
  classification (``kind`` / ``runnable`` / ``hint``). system_admin only.

* **Phase 2 — auto-mount proof (§5.1).** Drive a *real* agent that has the
  imported skill bound + the ``bash`` tool, asking it to ``cat`` the seeded
  ``SKILL.md`` out of ``/workspace/skills/<name>/``. If the file is there, the
  supervisor (#736) + orchestrator (#737) seeding works end-to-end under the
  real sandbox runtime. This is the half CI can't do (no Docker/model in CI).

Keyless: the model key is resolved server-side; this script only sends prompts.
The API token (a **system_admin** dev-login bearer) is read from
``HELIX_API_TOKEN`` and never logged.

Usage (bring the dev stack up first — ``make dev-up``)::

    export HELIX_API_URL=http://localhost:8000    # control-plane (8080 is Keycloak)
    export HELIX_API_TOKEN=<system_admin dev bearer token>

    # Phase 1 only (no agent needed):
    uv run python tools/eval/verify_live_skill_runtime.py --import-only

    # Both phases — the agent must bind the skill + grant `bash`
    # (and `image_variant: office` for the Anthropic doc skills):
    uv run python tools/eval/verify_live_skill_runtime.py --agent pptx-agent@1.0.0

Defaults import ``anthropics/skills`` → ``skills/pptx``. Exit code is non-zero
when any enabled phase fails — so it can gate a manual release check.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import httpx


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is not set — export it before running (see module docstring)")
    return value


def _unwrap(data: dict[str, Any]) -> dict[str, Any]:
    """Unwrap the ``{success, data, error}`` envelope (sessions/agents routes)."""
    if data.get("success") is False:
        err = data.get("error") or {}
        raise SystemExit(f"API error: {err.get('code')}: {err.get('message')}")
    inner = data.get("data")
    return inner if isinstance(inner, dict) else data


def _iter_messages(obj: Any) -> list[dict[str, Any]]:
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
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


# ── Phase 1 — import + classify ──────────────────────────────────────────────


async def phase_import(
    client: httpx.AsyncClient, *, source: str, skill: str
) -> tuple[bool, str | None]:
    """Import the skill from GitHub; assert a runtime classification is returned.

    Returns ``(ok, stored_skill_name)``. The platform skills routes are raw
    (NOT enveloped), so the response is read directly.
    """
    print(f"[phase 1] import {source} #{skill}")
    resp = await client.post(
        "/v1/platform/skills/import-from-github",
        json={"source": source, "skill": skill},
    )
    if resp.status_code not in (200, 201):
        print(f"  FAIL — import HTTP {resp.status_code}: {resp.text[:300]}")
        return False, None
    body = resp.json()
    name = (body.get("skill") or {}).get("name")
    runtime = body.get("runtime")
    print(f"  imported name={name!r} created={body.get('created')}")
    if not isinstance(runtime, dict):
        print("  FAIL — response carries no `runtime` classification (§5.2 regressed)")
        return False, name
    print(
        f"  runtime: kind={runtime.get('kind')} runnable={runtime.get('runnable')}\n"
        f"           hint={runtime.get('hint')}"
    )
    return True, name


# ── Phase 2 — auto-mount proof ───────────────────────────────────────────────

_SENTINEL = "HELIX_SEED_OK"


async def _create_session(client: httpx.AsyncClient, name: str, version: str) -> str:
    resp = await client.post("/v1/sessions", json={"agent_name": name, "agent_version": version})
    resp.raise_for_status()
    return str(_unwrap(resp.json())["thread_id"])


async def _run_collect_tools(client: httpx.AsyncClient, thread_id: str, prompt: str) -> list[str]:
    """Run the prompt; return every tool message's text (the bash output)."""
    tool_texts: list[str] = []
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
                    if _message_type(msg) in ("tool", "ToolMessage"):
                        tool_texts.append(_content_text(msg))
    return tool_texts


async def phase_mount(client: httpx.AsyncClient, *, agent: str, skill_name: str) -> bool:
    """Drive the agent to read the seeded SKILL.md; PASS if it's on disk."""
    name, _, version = agent.partition("@")
    if not version:
        raise SystemExit("--agent must be name@version")
    print(f"\n[phase 2] auto-mount proof via agent {agent}, skill={skill_name!r}")
    thread_id = await _create_session(client, name, version)
    print(f"  session: {thread_id}")

    target = f"/workspace/skills/{skill_name}/SKILL.md"
    prompt = (
        "Use the bash tool to run EXACTLY this command and report its full output "
        f"verbatim:\n`if [ -f {target} ]; then echo {_SENTINEL}; head -3 {target}; "
        f"else echo MISSING; ls -la /workspace/skills 2>&1; fi`"
    )
    tool_texts = await _run_collect_tools(client, thread_id, prompt)
    joined = "\n".join(tool_texts)
    print(f"  bash tool messages: {len(tool_texts)}")

    if _SENTINEL in joined:
        print(f"  PASS — {target} is present in the sandbox (auto-mount works).")
        return True
    print("  FAIL — seeded SKILL.md not found in /workspace/skills. Tool output:")
    print(
        "  "
        + (
            joined[:600].replace("\n", "\n  ")
            or "<no tool output — did the agent call bash? check it grants bash + binds the skill>"
        )
    )
    return False


# ── main ─────────────────────────────────────────────────────────────────────


async def _amain(args: argparse.Namespace) -> int:
    base_url = args.base_url or _require_env("HELIX_API_URL")
    token = _require_env("HELIX_API_TOKEN")  # never logged
    headers = {"Authorization": f"Bearer {token}"}

    ok = True
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=180.0) as client:
        import_ok, skill_name = await phase_import(client, source=args.source, skill=args.skill)
        ok = ok and import_ok
        if not args.import_only:
            if not args.agent:
                raise SystemExit("phase 2 needs --agent name@version (or pass --import-only)")
            target_name = args.skill_name or skill_name
            if not target_name:
                raise SystemExit("could not determine the stored skill name; pass --skill-name")
            ok = ok and await phase_mount(client, agent=args.agent, skill_name=target_name)

    print(f"\nRESULT: {'PASS — all phases green.' if ok else 'FAIL — see above.'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live skill runtime auto-mount verification.")
    parser.add_argument("--base-url", default=None, help="control-plane URL (or $HELIX_API_URL)")
    parser.add_argument("--source", default="anthropics/skills", help="GitHub source (owner/repo)")
    parser.add_argument("--skill", default="skills/pptx", help="skill folder path in the repo")
    parser.add_argument("--agent", default=None, help="agent name@version for phase 2")
    parser.add_argument(
        "--skill-name",
        default=None,
        help="stored skill name for the /workspace path (else taken from import response)",
    )
    parser.add_argument("--import-only", action="store_true", help="run phase 1 only")
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
