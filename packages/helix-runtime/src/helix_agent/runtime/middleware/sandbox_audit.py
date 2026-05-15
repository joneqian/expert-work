"""Sandbox-audit middleware — Stream E.10.

Registers to the ``before_tool_dispatch`` anchor and **only** activates
when the about-to-dispatch tool is a sandbox-executing one
(``exec_python`` / ``shell`` by default). All other tools — web_search,
HTTP, MCP — pass straight through.

For sandbox tools, walks the LLM-generated payload (``code`` for
Python, ``command`` for shell) against two denylists:

- **Python AST**: any ``ast.Call`` whose qualified target name is in
  :data:`_DENIED_PYTHON_CALLS` (``os.system``, ``subprocess.run``,
  ``eval``, ``exec``, ``compile``, ``__import__`` …) raises
  :class:`SandboxAuditBlockedError`.

- **Shell substring**: any of :data:`_DENIED_SHELL_SUBSTRINGS`
  appearing anywhere in the command line raises the same error.
  Covers ``rm -rf``, cloud-metadata exfil targets
  (``169.254.169.254`` / metadata DNS names), fork bomb, disk wipes.

Per [STREAM-E-DESIGN § 1.1 E.10](../../../../../../../docs/streams/STREAM-E-DESIGN.md),
this middleware ships **before** the F.4 ``exec_python`` adapter and
is harmless until that tool registers — non-sandbox tools always
``call_next`` unchanged. The blocking error type is local to this
module rather than reusing
``orchestrator.tools.ToolBlockedError`` so helix-runtime stays free
of a reverse dep on the orchestrator service; the ReAct graph's
``tools`` node already wraps **any** exception into
``ToolMessage(status="error")`` (Mini-ADR E-12).
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field

from helix_agent.runtime.middleware.base import CallNext, MiddlewareContext

logger = logging.getLogger(__name__)

DEFAULT_SANDBOX_TOOL_NAMES: frozenset[str] = frozenset({"exec_python", "shell"})

#: Qualified call names denied by the Python AST checker. Each entry
#: matches an ``ast.Call`` whose ``func`` resolves to the listed dotted
#: name (``os.system`` matches ``os.system(...)`` and
#: ``subprocess.run`` matches ``subprocess.run(...)``). Bare names
#: (``eval``, ``exec``, ``compile``, ``__import__``) match calls
#: without an attribute chain.
_DENIED_PYTHON_CALLS: frozenset[str] = frozenset(
    {
        "os.system",
        "os.popen",
        "os.execv",
        "os.execve",
        "os.execvp",
        "os.execvpe",
        "os.spawnl",
        "os.spawnv",
        "os.fork",
        "subprocess.call",
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.getoutput",
        "subprocess.getstatusoutput",
        "eval",
        "exec",
        "compile",
        "__import__",
    }
)

#: Substring patterns matched against shell commands. ``in`` test —
#: any occurrence triggers the block. Conservative on purpose:
#: ``rm -rf`` is rejected outright even if pointed at a safe path,
#: because the LLM's intent is the risk we're guarding (re-prompt for
#: a safer alternative).
_DENIED_SHELL_SUBSTRINGS: frozenset[str] = frozenset(
    {
        "rm -rf",
        "rm -fr",
        "rm --recursive",
        # Cloud-metadata IMDS exfil targets — never legitimate from
        # inside a sandbox.
        "169.254.169.254",
        "metadata.google.internal",
        "metadata.aliyun.com",
        "metadata.azure.com",
        # Fork bomb canonical form.
        ":(){:|:&};:",
        # Disk wipes.
        "mkfs",
        "dd if=/dev/zero",
        "dd if=/dev/random",
        "dd if=/dev/urandom",
    }
)


class SandboxAuditBlockedError(Exception):
    """LLM-generated code or command matched a denylist rule.

    The ReAct ``tools`` node (E.6) catches this like any other tool
    exception and turns it into a ``ToolMessage(status='error')`` so
    the LLM reasons about a safer alternative.
    """

    def __init__(self, rule: str, snippet: str) -> None:
        truncated = snippet[:200] + ("..." if len(snippet) > 200 else "")
        super().__init__(
            f"sandbox_audit blocked: matched rule {rule!r} in code/command: {truncated}"
        )
        self.rule = rule
        self.snippet = snippet


@dataclass
class SandboxAuditMiddleware:
    """Block dangerous code/commands before the sandbox dispatches them.

    Reads two keys from ``ctx.payload``:

    - ``tool_name: str`` — current tool about to dispatch. Only
      ``tool_name in sandbox_tool_names`` triggers the audit; everything
      else passes through unchanged.
    - ``tool_args: Mapping[str, Any]`` — args the LLM produced. For
      ``exec_python`` the ``code`` (or ``script``) key holds the
      Python source; for ``shell`` the ``command`` (or ``cmd``) key
      holds the command line.

    Missing keys / non-string values silently pass through — the
    sandbox executor itself will reject invalid input. This middleware
    is a security check, not an input validator.
    """

    sandbox_tool_names: frozenset[str] = field(default_factory=lambda: DEFAULT_SANDBOX_TOOL_NAMES)

    name: str = "sandbox_audit"
    anchor: str = "before_tool_dispatch"
    after: tuple[str, ...] = field(default_factory=tuple)
    before: tuple[str, ...] = field(default_factory=tuple)

    async def __call__(self, ctx: MiddlewareContext, call_next: CallNext) -> None:
        tool_name = ctx.payload.get("tool_name", "")
        if not isinstance(tool_name, str) or tool_name not in self.sandbox_tool_names:
            await call_next(ctx)
            return

        tool_args = ctx.payload.get("tool_args") or {}
        if isinstance(tool_args, dict):
            self._inspect(tool_name, tool_args)

        await call_next(ctx)

    def _inspect(self, tool_name: str, tool_args: dict[str, object]) -> None:
        if tool_name == "exec_python":
            code = self._first_string(tool_args, ("code", "script"))
            if code is not None:
                self._check_python(code)
        elif tool_name == "shell":
            command = self._first_string(tool_args, ("command", "cmd"))
            if command is not None:
                self._check_shell(command)

    @staticmethod
    def _first_string(args: dict[str, object], keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = args.get(key)
            if isinstance(value, str):
                return value
        return None

    @staticmethod
    def _check_python(code: str) -> None:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            # Let the sandbox executor surface the syntax error to the
            # LLM — we'd rather not raise SandboxAuditBlockedError for
            # something that's a parse failure, not a malicious call.
            return
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            qualified = _qualified_call_name(node.func)
            if qualified in _DENIED_PYTHON_CALLS:
                logger.warning("sandbox_audit.python_blocked rule=%s", qualified)
                raise SandboxAuditBlockedError(qualified, code)

    @staticmethod
    def _check_shell(command: str) -> None:
        for substring in _DENIED_SHELL_SUBSTRINGS:
            if substring in command:
                logger.warning("sandbox_audit.shell_blocked rule=%s", substring)
                raise SandboxAuditBlockedError(substring, command)


def _qualified_call_name(node: ast.expr) -> str:
    """Walk ``ast.Attribute`` / ``ast.Name`` chains to produce a dotted name.

    Returns ``""`` for anything we can't statically resolve to a
    dotted path (lambda call, subscript-callable, etc.) so the caller
    can ignore it — those aren't in the denylist anyway.
    """
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return ""
