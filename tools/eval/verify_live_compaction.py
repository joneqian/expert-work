"""Live compaction-depth verification — RT-2 PR-6 (★5).

Drives a **real** agent on a strict OpenAI-compatible domestic backend
(qwen / glm / deepseek) over a long conversation until the context
compressor fires, then asserts the RT-2 compaction depth work (PR-1..PR-5)
actually holds live — the half CI can't reach (no model key in CI):

  1. **No 400 when a compaction lands.** helix wraps the summary as a
     mid-conversation ``<context-summary>`` SystemMessage; strict backends
     (vLLM / qwen / glm) reject a non-leading ``system`` role. RT-ADR-5's
     adapter-level ``coalesce_system_messages`` must fold it into the
     leading system block — the headline live-bug fix. A live error frame
     here means coalescing regressed.
  2. **A COMPACTION event is emitted** (PR-4 observability): an SSE
     ``event: compaction`` with numeric ``{passes, tokens_before,
     tokens_after, summary_chars}`` and ``tokens_after < tokens_before``.
  3. **Key facts survive the compression.** A distinctive code seeded in
     turn 1 is still answerable after the summary replaces the middle —
     the compressor's head/tail + summary preserved it.

Keyless by construction: the model key lives in the server's DB and is
resolved server-side; this script only sends prompts + reads SSE. The API
token is read from ``HELIX_API_TOKEN`` and never logged. The ``client`` is
injectable so the harness logic is CI-covered with an ``httpx.MockTransport``
(see ``test_verify_live_compaction.py``) — the real run is a manual step.

Usage (bring the dev stack up first — ``make dev-up`` — with a qwen/glm/
deepseek agent active)::

    export HELIX_API_URL=http://localhost:8080     # your control-plane URL
    export HELIX_API_TOKEN=<a dev-login bearer token>
    uv run python tools/eval/verify_live_compaction.py            # auto-pick a strict agent
    uv run python tools/eval/verify_live_compaction.py --agent my-agent@1.0.0 --max-turns 40

Exit code is non-zero when the verification did not hold — a live error
frame (RT-ADR-5 regressed), no compaction ever fired (bump ``--max-turns``
or the agent's ``context_window`` is huge), or the seeded fact was lost.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from verify_live import (  # type: ignore[import-not-found]  # noqa: E402
    _content_text,
    _iter_messages,
    _message_type,
    _pick_agent,
    _require_env,
    _unwrap,
)

#: Strict OpenAI-compatible backends — the ones that 400 on a non-leading
#: ``system`` message (RT-ADR-5's target). Prefer these when auto-picking so
#: the run actually exercises the coalescing fix.
_STRICT_PROVIDERS = frozenset({"qwen", "glm", "deepseek", "self-hosted"})


@dataclass
class _Observed:
    """What one streamed run surfaced."""

    assistant_turns: int = 0
    last_text: str = ""
    #: COMPACTION event payloads seen this run (PR-4 ``event: compaction``).
    compactions: list[dict[str, Any]] = field(default_factory=list)
    #: An SSE ``event: error`` landed — RT-ADR-5 regression signal on a
    #: strict backend (a mid-conversation system message rejected as 400).
    errored: bool = False
    error_text: str = ""


async def _run_once(client: httpx.AsyncClient, thread_id: str, prompt: str) -> _Observed:
    """POST a run, stream the SSE, capture compaction / error / assistant text."""
    obs = _Observed()
    event = ""
    async with client.stream(
        "POST", f"/v1/sessions/{thread_id}/runs", json={"input": prompt}
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                raw = line[len("data: ") :]
                if event == "compaction":
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        obs.compactions.append(payload)
                elif event == "error":
                    obs.errored = True
                    obs.error_text = raw
                elif event == "updates":
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    for msg in _iter_messages(payload):
                        if _message_type(msg) in ("ai", "AIMessageChunk", "AIMessage", "assistant"):
                            text = _content_text(msg)
                            if text.strip():
                                obs.assistant_turns += 1
                                obs.last_text = text
    return obs


def _filler(index: int, fill_chars: int) -> str:
    """A large low-signal context block — asks the model to just ack, so the
    turn spends input tokens without generating a long reply."""
    body = ("The quick brown fox jumps over the lazy dog. " * ((fill_chars // 45) + 1))[:fill_chars]
    return f"Context block #{index} (reply only 'ok'):\n\n{body}"


async def run_compaction_check(
    client: httpx.AsyncClient,
    *,
    fact_code: str,
    agent_override: str | None = None,
    max_fill_turns: int = 30,
    fill_chars: int = 8000,
) -> int:
    """Drive a real agent until compaction fires; assert the three properties.

    Returns 0 on PASS, 1 on FAIL. Injectable ``client`` → MockTransport-testable.
    """
    name, version, provider = await _pick_agent(client, agent_override)
    strict = provider in _STRICT_PROVIDERS
    print(f"agent: {name}@{version}  provider={provider or '?'}  strict_backend={strict}")
    if not strict and agent_override is None:
        print(
            "  ! WARNING: no strict-backend (qwen/glm/deepseek) agent found — the "
            "RT-ADR-5 400 path is only truly exercised on a strict backend."
        )

    resp = await client.post("/v1/sessions", json={"agent_name": name, "agent_version": version})
    resp.raise_for_status()
    thread_id = str(_unwrap(resp.json())["thread_id"])
    print(f"session: {thread_id}\n")

    # Turn 1 — seed a distinctive fact the summary must preserve.
    seed = (
        f"Please remember this exact account code for later: {fact_code}. "
        "I will ask you to repeat it at the end. Reply only 'noted'."
    )
    obs = await _run_once(client, thread_id, seed)
    print(f"[seed] fact={fact_code}  reply={obs.last_text[:60]!r}")
    if obs.errored:
        print(f"RESULT: FAIL — error on the seed turn: {obs.error_text[:200]}")
        return 1

    # Turns 2..N — pile on context until a COMPACTION event lands.
    compaction_seen = 0
    for i in range(1, max_fill_turns + 1):
        obs = await _run_once(client, thread_id, _filler(i, fill_chars))
        compaction_seen += len(obs.compactions)
        for c in obs.compactions:
            before = c.get("tokens_before")
            after = c.get("tokens_after")
            print(
                f"[compaction] turn={i} passes={c.get('passes')} "
                f"{before} -> {after} tokens  summary_chars={c.get('summary_chars')}"
            )
            if isinstance(before, int) and isinstance(after, int) and after >= before:
                print("  ! WARNING: tokens_after >= tokens_before (compression did not shrink)")
        if obs.errored:
            # RT-ADR-5 — a strict backend 400 on the mid-conversation summary
            # surfaces as an error frame right after compaction fired.
            print(
                f"RESULT: FAIL — error frame after compaction (RT-ADR-5?): {obs.error_text[:200]}"
            )
            return 1
        if compaction_seen:
            break

    if compaction_seen == 0:
        print(
            f"\nRESULT: FAIL — no COMPACTION event after {max_fill_turns} fill turns. "
            "Raise --max-turns, raise --fill-chars, or the agent's context_window is very large."
        )
        return 1

    # Final probe — the seeded fact must survive the summary.
    probe = (
        "What exact account code did I ask you to remember at the very start? "
        "Reply with just the code."
    )
    obs = await _run_once(client, thread_id, probe)
    if obs.errored:
        print(f"RESULT: FAIL — error on the probe turn: {obs.error_text[:200]}")
        return 1
    recalled = fact_code in obs.last_text
    print(f"\n[probe] reply={obs.last_text[:120]!r}  fact_recalled={recalled}")

    if not recalled:
        print("RESULT: FAIL — the seeded fact was lost across compaction.")
        return 1
    print(
        f"RESULT: PASS — {compaction_seen} compaction(s) fired with no error "
        "(RT-ADR-5 coalescing holds), and the seeded fact survived."
    )
    return 0


async def _amain(args: argparse.Namespace) -> int:
    base_url = args.base_url or _require_env("HELIX_API_URL")
    token = _require_env("HELIX_API_TOKEN")  # never logged
    fact_code = args.fact_code or f"HELIX-{uuid4().hex[:8].upper()}"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=180.0) as client:
        return await run_compaction_check(
            client,
            fact_code=fact_code,
            agent_override=args.agent,
            max_fill_turns=args.max_turns,
            fill_chars=args.fill_chars,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live compaction-depth verification (RT-2 PR-6).")
    parser.add_argument("--base-url", default=None, help="control-plane URL (or $HELIX_API_URL)")
    parser.add_argument("--agent", default=None, help="target agent as name@version (else auto)")
    parser.add_argument("--max-turns", type=int, default=30, help="max fill turns before giving up")
    parser.add_argument("--fill-chars", type=int, default=8000, help="chars per fill turn (<=8192)")
    parser.add_argument("--fact-code", default=None, help="override the seeded fact (else random)")
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
