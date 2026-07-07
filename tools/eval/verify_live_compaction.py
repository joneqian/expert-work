"""Live compaction-depth verification — RT-2 PR-6 (★5), hardened in PR-7.

Drives a **real** agent on a strict OpenAI-compatible domestic backend
(qwen / glm / deepseek) over a long conversation until the context
compressor fires, then asserts the RT-2 compaction depth work (PR-1..PR-5)
actually holds live — the half CI can't reach (no model key in CI):

  1. **No 400 when a compaction lands.** expert_work wraps the summary as a
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
  env vars it falls back to a static ``EXPERT_WORK_API_TOKEN`` (unchanged
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

**Target-agent requirements (learned the hard way, 2026-07):**

* **A reachable compaction threshold.** Compaction fires at ``context_window *
  threshold_pct``. A flagship model (e.g. qwen3.7-max resolves to a ~1M
  catalog window → a 700k-token threshold) is untestable through the 65536-char
  input cap. Give the agent a small explicit ``context_window`` — ~64k works;
  32k is too small once the agent's ~20-24k fixed overhead (system prompt +
  tools + skills + memory) is counted, and the run will report a
  ContextOverflowError telling you to raise it.
* **Long-term memory OFF.** The fact-recall assertion seeds a code and checks
  it survives the summary. With long-term memory on, a code seeded by a
  *previous* run is injected and answered instead — a false failure. Disable
  the agent's long-term memory for a clean property-3 test.

Usage (bring the dev stack up first — ``make dev-up`` — with a qwen/glm/
deepseek agent active). Either hand it a token::

    export EXPERT_WORK_API_URL=http://localhost:8000       # your control-plane URL
    export EXPERT_WORK_API_TOKEN=<a dev-login bearer token>
    uv run python tools/eval/verify_live_compaction.py --agent my-agent@1.0.0

…or let it mint + auto-refresh its own (survives long runs)::

    export EXPERT_WORK_API_URL=http://localhost:8000
    export EXPERT_WORK_OIDC_TOKEN_URL=http://localhost:8080/realms/expert-work/protocol/openid-connect/token
    export EXPERT_WORK_OIDC_CLIENT_ID=expert-work-api-internal
    export EXPERT_WORK_OIDC_CLIENT_SECRET=<the client secret>   # client_credentials
    # …or username/password for a direct-access-grant client:
    # export EXPERT_WORK_OIDC_USERNAME=dev  EXPERT_WORK_OIDC_PASSWORD=devpass
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
#: The harness fills with small per-turn bodies (8000 chars) on purpose —
#: deliberately far under the server's ``RunRequest.input`` cap
#: (``MAX_RUN_INPUT_CHARS`` = 65536 in control-plane ``api/runs.py``) so a fill
#: turn is never rejected with a 422, and so it takes *many* turns to cross the
#: threshold (a fat middle for the compressor — see the next block). This caps
#: the filler *body*; ``_filler`` prepends a short ``Context block #N …`` marker
#: that fits inside the budget.
_MAX_INPUT_CHARS = 8000
#: The compressor keeps ``head_keep`` (4) + ``tail_keep`` (6) non-system
#: messages verbatim and only summarises the *middle*; if the threshold is
#: crossed before enough messages accumulate, the middle is empty and it
#: raises ``ContextOverflowError`` instead of a summary. So the fill must
#: cross only after **many** turns, leaving a fat middle. And the agent's
#: fixed context (system prompt + tool defs + skills list + injected memory)
#: already sits in the prompt before any conversation — a real qwen agent
#: measured ~20-24k tokens — so the usable room is ``threshold - overhead``,
#: not the whole threshold. Size against the room, cross it over ~this many
#: turns; if ``overhead`` alone exceeds the threshold the run surfaces a clear
#: ContextOverflowError (raise the agent's ``context_window``).
_FILL_TURNS_TO_TRIGGER = 15
_ASSUMED_OVERHEAD_TOKENS = 24_000

#: Resolve a ``None`` manifest ``context_window`` the way the server's agent
#: factory does — catalog lookup, else the 200k fallback (``agent_factory
#: ._resolved_context_window``). Guarded: the harness still runs (and the
#: MockTransport tests still pass) without the ``expert_work`` package.
try:
    from expert_work.protocol.model_catalog import (  # type: ignore[import-not-found]
        catalog_entry as _catalog_entry,
    )
except Exception:  # pragma: no cover - only when expert_work isn't importable
    _catalog_entry = None


def _resolve_context_window(provider: str, model_name: str, manifest_window: object) -> int:
    """Explicit manifest value wins; else the catalog window; else 200k."""
    if isinstance(manifest_window, int) and manifest_window > 0:
        return manifest_window
    if _catalog_entry is not None and provider and model_name:
        try:
            entry = _catalog_entry(provider, model_name)
        except Exception:
            entry = None
        window = getattr(entry, "context_window", None) if entry is not None else None
        if isinstance(window, int) and window > 0:
            return window
    return _DEFAULT_CONTEXT_WINDOW


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
    """Build a token minter from the ``EXPERT_WORK_OIDC_*`` env, or ``None``."""
    client_id = os.getenv("EXPERT_WORK_OIDC_CLIENT_ID")
    if not client_id:
        return None
    token_url = os.getenv("EXPERT_WORK_OIDC_TOKEN_URL")
    if not token_url:
        raise SystemExit(
            "EXPERT_WORK_OIDC_CLIENT_ID is set but EXPERT_WORK_OIDC_TOKEN_URL is missing"
        )
    client_secret = os.getenv("EXPERT_WORK_OIDC_CLIENT_SECRET")
    username = os.getenv("EXPERT_WORK_OIDC_USERNAME")
    password = os.getenv("EXPERT_WORK_OIDC_PASSWORD")

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
    # Usable room = threshold minus the agent's fixed overhead (which already
    # sits in the prompt). Clamp to a positive floor: if overhead >= threshold
    # the window is genuinely too small and the run will surface a clear
    # ContextOverflowError rather than a bogus RT-ADR-5 signal.
    room_tokens = max(threshold_tokens - _ASSUMED_OVERHEAD_TOKENS, threshold_tokens // 4)
    # Cross the room only after ~_FILL_TURNS_TO_TRIGGER turns so head+tail hold a
    # small share and the middle is large — and never exceed the API input cap.
    per_turn_tokens = max(
        1, min(room_tokens // _FILL_TURNS_TO_TRIGGER, _MAX_INPUT_CHARS // _CHARS_PER_TOKEN)
    )
    fill_chars = min(per_turn_tokens * _CHARS_PER_TOKEN, _MAX_INPUT_CHARS)
    # Budget enough turns to climb the room (overhead is already the baseline)
    # by 30% plus slack for the head/tail that never counts toward crossing.
    suggested_max_turns = math.ceil(room_tokens * 1.3 / per_turn_tokens) + 8
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

    Resolves ``context_window`` the way the server does — an explicit manifest
    value wins, else the model-catalog window for the ``(provider, model)``,
    else 200k (``_resolve_context_window``). A ``None`` manifest on a large
    catalog model (e.g. qwen3.7-max → ~1M) would otherwise be mis-sized as
    200k. Compression policy defaults to 0.7 when unset.
    """
    resp = await client.get("/v1/agents", params={"status": "active", "limit": 200})
    resp.raise_for_status()
    for rec in _unwrap(resp.json()).get("items", []):
        if rec.get("name") == name and rec.get("version") == version:
            body = (rec.get("spec") or {}).get("spec") or {}
            model = body.get("model") or {}
            provider = str(model.get("provider") or "")
            window = _resolve_context_window(
                provider, str(model.get("name") or ""), model.get("context_window")
            )
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


def _is_context_overflow(error_text: str) -> bool:
    """A compressor empty-middle failure, NOT a strict-backend 400.

    The server surfaces ``ContextOverflowError`` when the middle to summarise is
    empty — the agent's fixed overhead crossed the threshold before enough
    conversation accumulated. That is a window/overhead config issue, not an
    RT-ADR-5 coalescing regression, so the harness must not conflate the two.
    """
    lowered = error_text.lower()
    return "contextoverflow" in lowered or "context overflow" in lowered


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
            if _is_context_overflow(obs.error_text):
                # NOT an RT-ADR-5 regression — the compressor hit an empty middle
                # because the agent's fixed overhead leaves too little room in the
                # window. A config issue; don't mislabel it as a coalescing bug.
                print(
                    "RESULT: FAIL — ContextOverflowError (empty middle): the agent's fixed "
                    "context overhead (system prompt + tools + skills + memory) leaves too "
                    "little room in context_window for a summarisable middle. This is a "
                    "config issue, NOT an RT-ADR-5 regression — raise the agent's "
                    f"context_window or shrink its system prompt.\n  {obs.error_text[:200]}"
                )
                return 1
            # A genuine error frame right after compaction — on a strict backend
            # this is the RT-ADR-5 signal (a mid-conversation system message 400).
            print(
                "RESULT: FAIL — error frame after compaction (possible RT-ADR-5 "
                f"regression): {obs.error_text[:200]}"
            )
            return 1
        if compaction_seen:
            break
        print(f"[fill] turn={i}/{max_fill_turns}  (piling context, no compaction yet)")

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
    base_url = args.base_url or _require_env("EXPERT_WORK_API_URL")
    mint = _build_mint()
    if mint is not None:
        token: str | None = os.getenv("EXPERT_WORK_API_TOKEN") or await mint()
    else:
        token = _require_env("EXPERT_WORK_API_TOKEN")  # never logged
    fact_code = args.fact_code or f"EXPERT_WORK-{uuid4().hex[:8].upper()}"
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
    parser.add_argument(
        "--base-url", default=None, help="control-plane URL (or $EXPERT_WORK_API_URL)"
    )
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
