"""Run-time Jinja rendering of an agent's ``system_prompt`` (Dynamic-Prompt).

Renders ONLY the human-authored ``base`` template (the ``system_prompt``
field) with the run's ``inputs``; the orchestrator-computed ``suffix``
(spotlight clause, skill bodies, memory blocks) is appended verbatim and
never itself Jinja-rendered — so a skill body containing literal ``{{ }}``
can't break the run, and untrusted memory/skill content can't become an
SSTI primitive. See ``docs/design/jinja-dynamic-prompt.md`` §3.

``trusted=False`` variables are spotlight-fenced as DATA before
substitution (shared nonce with the model-side tool/RAG channels);
``trusted=True`` (the owner-set default, §4) renders verbatim.
"""

from __future__ import annotations

from typing import Any

from jinja2 import TemplateError

from control_plane.manifest.loader import build_sandboxed_environment
from expert_work.common.spotlight import spotlight_untrusted

# ``built`` is the orchestrator ``BuiltAgent`` (typed ``Any`` here, matching
# ``build_run_graph_input``); the renderer reads ``system_prompt``,
# ``prompt_jinja``, ``prompt_variables`` (each with ``.name``/``.trusted``),
# ``prompt_base``, ``prompt_suffix``, ``spotlight_nonce``.


class PromptRenderError(ValueError):
    """Template render failed (bad syntax / undefined). Maps to 422."""


def _fence_value(value: str, *, nonce: str | None) -> str:
    """Wrap an untrusted value as DATA. With spotlighting off (no nonce)
    degrade to a plain marker — same backstop as ``untrusted_content``."""
    if nonce:
        return spotlight_untrusted(value, nonce=nonce)
    return f"[untrusted content]\n{value}"


def render_system_prompt(built: Any, inputs: dict[str, Any]) -> str:
    """Return the system prompt for one run.

    Non-Jinja agents (``prompt_jinja`` False — every existing agent) return
    the stored prompt unchanged: byte-identical, zero overhead, prompt cache
    intact. Jinja agents render ``prompt_base`` with the declared variables
    and append ``prompt_suffix`` verbatim.

    ``inputs`` is assumed already validated by :func:`validate_prompt_inputs`
    (undeclared / missing-required rejected at request time); this stays
    defensive — a missing value renders as the empty string.
    """
    # ``getattr`` default keeps older ``Any``-typed build doubles (and any
    # caller predating these fields) on the non-jinja path — byte-identical.
    if not getattr(built, "prompt_jinja", False):
        verbatim: str = built.system_prompt
        return verbatim

    context: dict[str, Any] = {}
    for var in built.prompt_variables:
        raw = inputs.get(var.name, "")
        if var.trusted:
            context[var.name] = raw
        else:
            context[var.name] = _fence_value(str(raw), nonce=built.spotlight_nonce)

    env = build_sandboxed_environment()
    try:
        rendered_base: str = env.from_string(built.prompt_base).render(**context)
    except TemplateError as exc:
        # No ``from exc``: the API layer surfaces a clean message and CodeQL's
        # py/stack-trace-exposure flags the chained cause if it reaches a body.
        raise PromptRenderError(f"system_prompt render failed: {exc}") from None
    suffix: str = built.prompt_suffix
    return rendered_base + suffix


def validate_prompt_inputs(built: Any, inputs: dict[str, Any]) -> None:
    """Validate a run's ``inputs`` against the agent's declared variables.

    Raises :class:`PromptRenderError` (caller maps to HTTP 422) so a bad
    request fails synchronously — including queue-mode runs, which validate
    before enqueue rather than blowing up later in the worker.
    """
    if not getattr(built, "prompt_jinja", False):
        if inputs:
            raise PromptRenderError("agent declares no prompt variables; 'inputs' not accepted")
        return
    declared = {v.name: v for v in built.prompt_variables}
    for key in inputs:
        if key not in declared:
            raise PromptRenderError(f"unknown input variable: {key}")
    for name, var in declared.items():
        if var.required and name not in inputs:
            raise PromptRenderError(f"missing required input: {name}")
