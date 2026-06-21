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

    # One-shot end-to-end (import → activate skill → register the bundled
    # manifests/pptx-skill-test agent → auto-mount proof). Edit the manifest's
    # model block to your configured provider first:
    uv run python tools/eval/verify_live_skill_runtime.py --setup

    # Phase 2 against an agent you already registered (binds the skill + bash,
    # image_variant: office):
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
from pathlib import Path
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
) -> tuple[bool, str | None, str | None]:
    """Import the skill from GitHub; assert a runtime classification is returned.

    Returns ``(ok, stored_skill_name, skill_id)``. The platform skills routes
    are raw (NOT enveloped), so the response is read directly.
    """
    print(f"[phase 1] import {source} #{skill}")
    resp = await client.post(
        "/v1/platform/skills/import-from-github",
        json={"source": source, "skill": skill},
    )
    if resp.status_code not in (200, 201):
        print(f"  FAIL — import HTTP {resp.status_code}: {resp.text[:300]}")
        return False, None, None
    body = resp.json()
    skill_obj = body.get("skill") or {}
    name = skill_obj.get("name")
    skill_id = skill_obj.get("id")
    runtime = body.get("runtime")
    print(f"  imported name={name!r} created={body.get('created')}")
    if not isinstance(runtime, dict):
        print("  FAIL — response carries no `runtime` classification (§5.2 regressed)")
        return False, name, skill_id
    print(
        f"  runtime: kind={runtime.get('kind')} runnable={runtime.get('runnable')}\n"
        f"           hint={runtime.get('hint')}"
    )
    return True, name, skill_id


async def activate_platform_skill(client: httpx.AsyncClient, *, skill_id: str) -> bool:
    """PATCH the platform skill to ACTIVE (imported skills start DRAFT, and an
    agent can only bind an ACTIVE platform skill). Idempotent."""
    print(f"[setup] activate platform skill {skill_id}")
    resp = await client.patch(f"/v1/platform/skills/{skill_id}", json={"status": "active"})
    if resp.status_code not in (200, 201):
        print(f"  FAIL — activate HTTP {resp.status_code}: {resp.text[:200]}")
        return False
    print(f"  status now: {resp.json().get('status')}")
    return True


async def register_agent(
    client: httpx.AsyncClient, *, manifest_path: str
) -> tuple[str, str] | None:
    """Register the agent from a manifest YAML; return ``(name, version)``.

    Idempotent: a 409 (already registered) is treated as success. Returns None
    on a real failure.
    """
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


# ── Phase 2 — auto-mount proof ───────────────────────────────────────────────


async def _create_session(client: httpx.AsyncClient, name: str, version: str) -> str:
    resp = await client.post("/v1/sessions", json={"agent_name": name, "agent_version": version})
    resp.raise_for_status()
    return str(_unwrap(resp.json())["thread_id"])


class _RunTrace:
    """What the SSE stream revealed — to tell apart 'no bash call' from a run error."""

    def __init__(self) -> None:
        self.tool_texts: list[str] = []
        self.assistant_text: str = ""
        self.errors: list[str] = []
        self.events: dict[str, int] = {}
        self.raw_updates: list[str] = []


async def _run_collect(client: httpx.AsyncClient, thread_id: str, prompt: str) -> _RunTrace:
    """Run the prompt; collect tool output + final assistant text + errors + the
    set of SSE event types seen (so a FAIL is diagnosable)."""
    tr = _RunTrace()
    event = ""
    async with client.stream(
        "POST", f"/v1/sessions/{thread_id}/runs", json={"input": prompt}
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
                tr.events[event] = tr.events.get(event, 0) + 1
            elif line.startswith("data: "):
                raw = line[len("data: ") :]
                if event in ("error", "run_error"):
                    tr.errors.append(raw[:300])
                    continue
                if event == "updates":
                    tr.raw_updates.append(raw)
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                for msg in _iter_messages(payload):
                    mtype = _message_type(msg)
                    if mtype in ("tool", "ToolMessage"):
                        tr.tool_texts.append(_content_text(msg))
                    elif mtype in ("ai", "AIMessage", "AIMessageChunk", "assistant"):
                        text = _content_text(msg)
                        if text.strip():
                            tr.assistant_text = text
    return tr


async def phase_mount(client: httpx.AsyncClient, *, agent: str, skill_name: str) -> bool:
    """Drive the agent to read the seeded SKILL.md; PASS if it's on disk."""
    name, _, version = agent.partition("@")
    if not version:
        raise SystemExit("--agent must be name@version")
    print(f"\n[phase 2] auto-mount proof via agent {agent}, skill={skill_name!r}")
    thread_id = await _create_session(client, name, version)
    print(f"  session: {thread_id}")

    target = f"/workspace/skills/{skill_name}/SKILL.md"
    # Probe via read_file (read-only → NOT approval-gated, unlike bash/exec_python
    # which declare side_effect=irreversible and would pause the run on approval).
    # read_file acquires a seeded sandbox too, so a successful read proves the
    # auto-mount. A present SKILL.md starts with the frontmatter `name: <skill>`.
    prompt = (
        f"Use the read_file tool to read the file {target} and report its content "
        "verbatim. If the tool errors, report the exact error."
    )
    sentinel = f"name: {skill_name}"
    tr = await _run_collect(client, thread_id, prompt)
    joined = "\n".join(tr.tool_texts)
    print(f"  sse events: {tr.events}")
    print(f"  tool messages: {len(tr.tool_texts)}")

    if sentinel in joined:
        print(f"  PASS — {target} read back from the sandbox (auto-mount works).")
        return True

    print("  FAIL — seeded SKILL.md not confirmed. Diagnostics:")
    if tr.errors:
        print("  run errors:")
        for e in tr.errors:
            print(f"    ! {e}")
    if tr.tool_texts:
        print("  tool output:")
        print("    " + joined[:600].replace("\n", "\n    "))
    else:
        print("  <no tool calls — model didn't call bash, or the run errored before tools>")
    if tr.assistant_text:
        print("  assistant said:")
        print("    " + tr.assistant_text[:500].replace("\n", "\n    "))
    if not tr.tool_texts and not tr.assistant_text:
        # Parser extracted nothing — dump raw `updates` so we can see what the
        # run actually streamed (error-in-state? different message shape?).
        print("  raw updates (nothing parsed — shape/error dump):")
        for raw in tr.raw_updates[:5]:
            print("    " + raw[:500].replace("\n", "\n    "))
    return False


# ── main ─────────────────────────────────────────────────────────────────────


_DEFAULT_MANIFEST = (
    Path(__file__).resolve().parents[2] / "manifests" / "pptx-skill-test" / "v1.0.0.yaml"
)


async def _amain(args: argparse.Namespace) -> int:
    base_url = args.base_url or _require_env("HELIX_API_URL")
    token = _require_env("HELIX_API_TOKEN")  # never logged
    headers = {"Authorization": f"Bearer {token}"}

    ok = True
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=180.0) as client:
        import_ok, skill_name, skill_id = await phase_import(
            client, source=args.source, skill=args.skill
        )
        ok = ok and import_ok
        target_name = args.skill_name or skill_name

        if args.setup:
            # One-shot: activate the imported skill → register the agent →
            # auto-mount proof. Each step idempotent.
            if skill_id:
                ok = ok and await activate_platform_skill(client, skill_id=skill_id)
            registered = await register_agent(client, manifest_path=args.manifest)
            if registered is None:
                ok = False
            else:
                name, version = registered
                if not target_name:
                    raise SystemExit("could not determine the stored skill name")
                ok = ok and await phase_mount(
                    client, agent=f"{name}@{version}", skill_name=target_name
                )
        elif not args.import_only:
            if not args.agent:
                raise SystemExit("phase 2 needs --agent name@version (or --import-only / --setup)")
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
    parser.add_argument(
        "--setup",
        action="store_true",
        help="one-shot: import → activate skill → register agent → auto-mount proof",
    )
    parser.add_argument(
        "--manifest",
        default=str(_DEFAULT_MANIFEST),
        help="agent manifest YAML for --setup (default: manifests/pptx-skill-test/v1.0.0.yaml)",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
