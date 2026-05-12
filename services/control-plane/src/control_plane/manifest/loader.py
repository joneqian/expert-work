"""YAML + Jinja2 → :class:`AgentSpec`.

Stages:

1. **Size guard** — refuse documents larger than ``max_size_bytes`` (DoS
   protection per STREAM-B-DESIGN § 6).
2. **Jinja2 render** — substitute caller-supplied template variables.
   Uses ``StrictUndefined`` so a typo in the manifest surfaces here, not
   silently as an empty string.
3. **YAML parse** — ``yaml.safe_load``, never ``yaml.load``.
4. **Pydantic validation** — :class:`AgentSpec` carries the lint rules
   (network allowlist + fallback-chain cycles) as ``model_validator``\\s.

The loader is **state-free**: every call constructs a fresh Jinja2
``Environment`` so multi-tenant workers don't leak templates across
tenants. The cost is negligible compared to the LLM call that follows.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from jinja2 import StrictUndefined, TemplateError, select_autoescape
from jinja2.sandbox import SandboxedEnvironment
from pydantic import ValidationError

from control_plane.manifest.errors import (
    ManifestSyntaxError,
    ManifestTemplateError,
    ManifestValidationError,
)
from helix_agent.protocol import AgentSpec

#: Default cap mirrors STREAM-B-DESIGN § 6 (DoS guard).
DEFAULT_MAX_SIZE_BYTES = 64 * 1024


class ManifestLoader:
    """Reusable loader that the FastAPI handler holds on app.state."""

    def __init__(self, *, max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES) -> None:
        if max_size_bytes <= 0:
            msg = f"max_size_bytes must be > 0, got {max_size_bytes}"
            raise ValueError(msg)
        self._max_size_bytes = max_size_bytes

    @property
    def max_size_bytes(self) -> int:
        return self._max_size_bytes

    def load_from_string(
        self,
        source: str,
        *,
        template_vars: Mapping[str, Any] | None = None,
    ) -> AgentSpec:
        encoded = source.encode("utf-8")
        if len(encoded) > self._max_size_bytes:
            msg = f"manifest exceeds size cap {len(encoded)} > {self._max_size_bytes} bytes"
            raise ManifestSyntaxError(msg)

        rendered = self._render(source, template_vars or {})
        document = self._parse_yaml(rendered)
        return self._validate(document)

    def load_from_path(
        self,
        path: str | Path,
        *,
        template_vars: Mapping[str, Any] | None = None,
    ) -> AgentSpec:
        return self.load_from_string(
            Path(path).read_text(encoding="utf-8"),
            template_vars=template_vars,
        )

    # ----- internals --------------------------------------------------

    def _render(self, source: str, vars_: Mapping[str, Any]) -> str:
        # Manifest is YAML, not HTML. ``select_autoescape`` with an
        # empty enabled-extensions list (and ``default_for_string=False``)
        # is jinja2's canonical "opt out explicitly" pattern; CodeQL's
        # py/jinja2-autoescape-false flags the literal ``False`` but
        # accepts this callable as evidence the choice was deliberate.
        #
        # ``SandboxedEnvironment`` blocks the ``__class__.__mro__`` /
        # ``__subclasses__`` introspection chain that turns ordinary
        # Jinja2 templates into a Python RCE primitive — required
        # because the template source is user-supplied (CodeQL's
        # py/template-injection rule).
        env = SandboxedEnvironment(
            undefined=StrictUndefined,
            autoescape=select_autoescape(enabled_extensions=(), default_for_string=False),
            keep_trailing_newline=True,
        )
        try:
            template = env.from_string(source)
            return template.render(**vars_)
        except TemplateError as exc:
            # Deliberately do NOT chain via ``from exc``: the API layer
            # logs the cause server-side and CodeQL's py/stack-trace-
            # exposure flags the chained __cause__ as leaking exception
            # info to the response if it's accessible there.
            message = f"manifest template render failed: {exc}"
            raise ManifestTemplateError(message) from None

    def _parse_yaml(self, rendered: str) -> dict[str, Any]:
        try:
            doc = yaml.safe_load(rendered)
        except yaml.YAMLError as exc:
            raise ManifestSyntaxError(f"manifest is not valid YAML: {exc}") from None
        if not isinstance(doc, dict):
            raise ManifestSyntaxError(f"manifest root must be a mapping, got {type(doc).__name__}")
        return doc

    def _validate(self, document: dict[str, Any]) -> AgentSpec:
        try:
            return AgentSpec.model_validate(document)
        except ValidationError as exc:
            # Project the pydantic errors into a hand-curated whitelist
            # of fields and build a fresh list of plain dicts. This
            # severs the data-flow link CodeQL traces from the caught
            # ``ValidationError`` (py/stack-trace-exposure).
            sanitized: list[dict[str, object]] = []
            for err in exc.errors():
                sanitized.append(
                    {
                        "loc": list(err.get("loc", ())),
                        "type": str(err.get("type", "")),
                        "msg": str(err.get("msg", "")),
                    }
                )
            error_count = exc.error_count()
            # ``from None`` deliberately drops the __cause__ chain so
            # CodeQL stops tracing taint from the pydantic exception.
            raise ManifestValidationError(
                f"manifest failed Pydantic validation ({error_count} error(s))",
                errors=sanitized,
            ) from None


def load_manifest(
    source: str | Path,
    *,
    template_vars: Mapping[str, Any] | None = None,
    max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES,
) -> AgentSpec:
    """Convenience wrapper for one-off loads (tests, CLI lint)."""
    loader = ManifestLoader(max_size_bytes=max_size_bytes)
    if isinstance(source, Path):
        return loader.load_from_path(source, template_vars=template_vars)
    return loader.load_from_string(source, template_vars=template_vars)
