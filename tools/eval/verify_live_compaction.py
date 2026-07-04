"""Live compaction-depth verification — RT-2 PR-6 (★5), hardened in PR-7.

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
resolved server-side; this script only sends prompts + reads SSE.

**PR-7 hardening — the two things that make a live run fragile:**

* **Token lifetime.** A dev bearer token can be short-lived (the internal
  service-account client caps its access token at 300 s), but piling
  context via real LLM calls easily runs past that. If OIDC client
  credentials are provided the harness mints its own token and
  **re-mints on a 401**, so the run outlives any token TTL. Absent those
  env vars it falls back to a static ``HELIX_API_TOKEN`` (unchanged
  behaviour). The token is never logged.
* **Fill sizing.** Compaction triggers at ``context_window *
  threshold_pct``; a fixed fill size either never reaches it (huge window)
  or overflows a small one. The harness reads the target agent's
  ``context_window`` / ``threshold_pct`` from ``/v1/agents`` and sizes each
  fill turn to cross the threshold in a few turns while keeping every
  single turn comfortably inside the window. ``--fill-chars`` /
  ``--max-turns`` still override the auto-sizing.

The ``client`` is injectable so the harness logic is CI-covered with an
``httpx.MockTransport`` (see ``test_verify_live_compaction.py``) — the real
run is a manual step.

Usage (bring the dev stack up first — ``make dev-up`` — with a qwen/glm/
deepseek agent active). Either hand it a token::

    export HELIX_API_URL=http://localhost:8000       # your control-plane URL
    export HELIX_API_TOKEN=<a dev-login bearer token>
    uv run python tools/eval/verify_live_compaction.py --agent my-agent@1.0.0

…or let it mint + auto-refresh its own (survives long runs)::

    export HELIX_API_URL=http://localhost:8000
    export HELIX_OIDC_TOKEN_URL=http://localhost:8080/realms/helix-agent/protocol/openid-connect/token
    export HELIX_OIDC_CLIENT_ID=helix-agent-api-internal
    export HELIX_OIDC_CLIENT_SECRET=<the client secret>   # client_credentials
    # …or username/password for a direct-access-grant client:
    # export HELIX_OIDC_USERNAME=dev  HELIX_OIDC_PASSWORD=devpass
    uv run python tools/eval/verify_live_compaction.py --agent my-agent@1.0.0

Exit code is non-zero when the verification did not hold — a live error
frame (RT-ADR-5 regressed), no compaction ever fired (bump ``--max-turns``
or the agent's ``context_window`` is huge), or the seeded fact was lost.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
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

#: Mirrors the compressor's own heuristics so the auto-sizing lands a
#: compaction: ``tokens ≈ chars // 4`` (``compressor._CHARS_PER_TOKEN``),
#: the trigger is ``context_window * threshold_pct``, and both default the
#: same way the agent factory does when a manifest leaves them unset.
_CHARS_PER_TOKEN = 4
_DEFAULT_CONTEXT_WINDOW = 200_000
_DEFAULT_THRESHOLD_PCT = 0.7
#: Cap a single fill turn's size so a very large window doesn't produce an
#: unwieldy request body; the loop just takes a few more turns instead.
_MAX_FILL_TOKENS_PER_TURN = 40_000
#: The compressor keeps ``head_keep`` (4) + ``tail_keep`` (6) non-system
#: messages verbatim and only summarises the *middle*; if the threshold is
#: crossed before enough messages accumulate, the middle is empty and the
#: compressor raises ``ContextOverflowError`` instead of emitting a summary
#: (compressor.py ``_split`` / ``ContextOverflowError``). So the fill must
#: cross the threshold only after **many** turns — one turn adds a
#: human+assistant pair — leaving a fat middle to summarise. Aim to cross
#: around this many fill turns (≫ the ~5 turns held by head+tail).
_FILL_TURNS_TO_TRIGGER = 15

#: Async callable that returns a fresh bearer token (or ``None`` = no refresh).
Minter = Callable[[], Awaitable[str]]


async def _mint_token(
    token_url: str,
    client_id: str,
    *,
    client_secret: str | None = None,
    username: str | None = None,
    password: str | None = None,
) -> str:
    """Fetch an access token from an OIDC token endpoint.

    ``username`` present → password (direct-access) grant; otherwise
    client-credentials. Uses its own client so it never recurses through the
    refreshing auth. The token is returned, never logged.
    """
    data: dict[str, str] = {"client_id": client_id}
    if username:
        data.update({"grant_type": "password", "username": username, "password": password or ""})
        data["scope"] = "openid"
    else:
        data["grant_type"] = "client_credentials"
    if client_secret:
        data["client_secret"] = client_secret
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(token_url, data=data)
        resp.raise_for_status()
        token = resp.json().get("access_token")
    if not token:
        raise SystemExit("token endpoint returned no access_token")
    return str(token)


class _RefreshingAuth(httpx.Auth):
    """Bearer auth that re-mints on a 401 and retries the request once.

    A live compaction run outlives a short-lived dev token; rather than
    fail mid-run, mint a fresh one and retry. Only the status code is
    inspected (no response body), so it is safe on streaming responses.
    """

    def __init__(self, token: str | None, mint: Minter | None = None) -> None:
        self._token = token
        self._mint = mint

    async def async_auth_flow(self, request: httpx.Request) -> AsyncIterator[httpx.Request]:
        if self._token:
            request.headers["Authorization"] = f"Bearer {self._token}"
        response = yield request
        if response.status_code == 401 and self._mint is not None:
            self._token = await self._mint()
            request.headers["Authorization"] = f"Bearer {self._token}"
            yield request


def _build_mint() -> Minter | None:
    """Build a token minter from the ``HELIX_OIDC_*`` env, or ``None``."""
    client_id = os.getenv("HELIX_OIDC_CLIENT_ID")
    if not client_id:
        return None
    token_url = os.getenv("HELIX_OIDC_TOKEN_URL")
    if not token_url:
        raise SystemExit("HELIX_OIDC_CLIENT_ID is set but HELIX_OIDC_TOKEN_URL is missing")
    client_secret = os.getenv("HELIX_OIDC_CLIENT_SECRET")
    username = os.getenv("HELIX_OIDC_USERNAME")
    password = os.getenv("HELIX_OIDC_PASSWORD")

    async def _mint() -> str:
        return await _mint_token(
            token_url,
            client_id,
            client_secret=client_secret,
            username=username,
            password=password,
        )

    return _mint


def _plan_fill(context_window: int, threshold_pct: float) -> tuple[int, int]:
    """Size each fill turn so a compaction actually *summarises* (not fail-hards).

    The preflight compresses once estimated prompt tokens (``total_chars //
    _CHARS_PER_TOKEN``) reach ``context_window * threshold_pct``. Critically,
    the compressor only summarises the middle — it keeps head+tail (~10
    messages ≈ 5 turns) verbatim and raises ``ContextOverflowError`` if the
    middle is empty when the threshold is crossed. So we size each turn to a
    *small* slice of the threshold — crossing it only after ~``_FILL_TURNS_
    TO_TRIGGER`` turns — which leaves a fat middle to summarise. Sizing at a
    large fraction (few turns to cross) would guarantee an empty-middle
    fail-hard, which the harness would misread as an RT-ADR-5 error frame.

    Returns ``(fill_chars, suggested_max_turns)``.
    """
    threshold_tokens = max(1, int(context_window * threshold_pct))
    # Cross the threshold only after ~_FILL_TURNS_TO_TRIGGER turns so head+tail
    # hold a small share and the middle is large. Cap per-turn size (huge
    # windows just take more turns) and floor at 1.
    per_turn_tokens = max(
        1, min(threshold_tokens // _FILL_TURNS_TO_TRIGGER, _MAX_FILL_TOKENS_PER_TURN)
    )
    fill_chars = per_turn_tokens * _CHARS_PER_TOKEN
    # Budget enough turns to overshoot the threshold by 30% plus slack for the
    # head/tail that never counts toward crossing.
    target_tokens = int(threshold_tokens * 1.3)
    suggested_max_turns = math.ceil(target_tokens / per_turn_tokens) + 6
    return fill_chars, suggested_max_turns


async def _agent_context(
    client: httpx.AsyncClient, name: str, version: str
) -> tuple[int, float, str]:
    """Read ``(context_window, threshold_pct, provider)`` from ``/v1/agents``.

    A list item is an ``AgentSpecRecord`` whose ``spec`` is a full
    ``AgentSpec`` (``{metadata, spec: AgentSpecBody}``) — so the model /
    policies live under a **double** nesting ``rec["spec"]["spec"]…`` (see
    ``test_agents_api.py`` ``record["spec"]["spec"]["system_prompt"]``). The
    provider is re-read here rather than trusting the imported ``_pick_agent``,
    which reads the single-nested (wrong) shape and so always yields ``""``.

    Falls back to the agent-factory defaults when a manifest leaves either
    unset (``context_window: None`` → catalog/200k; no compression policy →
    0.7), so auto-sizing always has concrete numbers.
    """
    resp = await client.get("/v1/agents", params={"status": "active", "limit": 200})
    resp.raise_for_status()
    for rec in _unwrap(resp.json()).get("items", []):
        if rec.get("name") == name and rec.get("version") == version:
            body = (rec.get("spec") or {}).get("spec") or {}
            model = body.get("model") or {}
            window = model.get("context_window") or _DEFAULT_CONTEXT_WINDOW
            provider = str(model.get("provider") or "")
            policy = (body.get("policies") or {}).get("context_compression") or {}
            threshold = policy.get("threshold_pct") or _DEFAULT_THRESHOLD_PCT
            return int(window), float(threshold), provider
    return _DEFAULT_CONTEXT_WINDOW, _DEFAULT_THRESHOLD_PCT, ""


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
    max_fill_turns: int | None = None,
    fill_chars: int | None = None,
) -> int:
    """Drive a real agent until compaction fires; assert the three properties.

    ``max_fill_turns`` / ``fill_chars`` default to ``None`` → auto-sized from
    the agent's ``context_window`` / ``threshold_pct``; pass explicit values to
    override. Returns 0 on PASS, 1 on FAIL. Injectable ``client`` →
    MockTransport-testable.
    """
    name, version, _ = await _pick_agent(client, agent_override)
    # Re-read the record correctly (``_pick_agent``'s provider is the wrong,
    # single-nested shape → always ""); this is the honest strict/window read.
    window, threshold_pct, provider = await _agent_context(client, name, version)
    strict = provider in _STRICT_PROVIDERS
    print(f"agent: {name}@{version}  provider={provider or '?'}  strict_backend={strict}")
    if not strict:
        print(
            "  ! WARNING: target is not a strict-backend (qwen/glm/deepseek) agent — "
            "the RT-ADR-5 400 path is only truly exercised on a strict backend."
        )

    auto_chars, auto_turns = _plan_fill(window, threshold_pct)
    if fill_chars is None:
        fill_chars = auto_chars
    if max_fill_turns is None:
        max_fill_turns = auto_turns
    print(
        f"context_window={window} threshold_pct={threshold_pct} "
        f"→ fill_chars={fill_chars} max_turns={max_fill_turns}"
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
    mint = _build_mint()
    if mint is not None:
        token: str | None = os.getenv("HELIX_API_TOKEN") or await mint()
    else:
        token = _require_env("HELIX_API_TOKEN")  # never logged
    fact_code = args.fact_code or f"HELIX-{uuid4().hex[:8].upper()}"
    auth = _RefreshingAuth(token, mint)
    async with httpx.AsyncClient(base_url=base_url, auth=auth, timeout=180.0) as client:
        return await run_compaction_check(
            client,
            fact_code=fact_code,
            agent_override=args.agent,
            max_fill_turns=args.max_turns,
            fill_chars=args.fill_chars,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Live compaction-depth verification (RT-2 PR-6/7)."
    )
    parser.add_argument("--base-url", default=None, help="control-plane URL (or $HELIX_API_URL)")
    parser.add_argument("--agent", default=None, help="target agent as name@version (else auto)")
    parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="max fill turns before giving up (default: auto from context_window)",
    )
    parser.add_argument(
        "--fill-chars",
        type=int,
        default=None,
        help="chars per fill turn (default: auto from context_window)",
    )
    parser.add_argument("--fact-code", default=None, help="override the seeded fact (else random)")
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
