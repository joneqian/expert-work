"""CI lint: keep ``INSERT INTO audit_log`` confined to the audit writer path.

Per STREAM-D-DESIGN § 6 (risk row "应用代码忘了 SET ROLE audit_writer"):
the only code path that may emit an INSERT into ``audit_log`` is
:mod:`helix_agent.persistence.audit_log.sql`, because that path runs
``SET LOCAL ROLE audit_writer`` first. Any other call site:

* Will hit a "permission denied" from the DB at runtime when running
  as the production app role.
* Defeats the audit_writer / REVOKE design by going through whatever
  role the bare connection happens to use.

The lint scans every ``*.py`` and ``*.sql`` under ``packages`` and
``services`` for ``INSERT INTO audit_log`` (case-insensitive, optional
schema-qualified name) and fails if a hit lands outside the
allowlisted writer file or the audit-test corpus (tests intentionally
poke raw SQL to verify the REVOKE).

Run via ``python tools/persistence/check_audit_insert_path.py``.
Exits 0 when clean, 1 otherwise.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Final

# Files allowed to mention ``INSERT INTO audit_log`` literally.
_ALLOWLIST_SUFFIXES: Final[tuple[str, ...]] = (
    # The canonical writer.
    "packages/helix-persistence/src/helix_agent/persistence/audit_log/sql.py",
    # The lint script itself (mentions the string in its docstring).
    "tools/persistence/check_audit_insert_path.py",
    # Tests that probe the REVOKE / write-via-SET-ROLE behavior with raw SQL.
    "packages/helix-persistence/tests/test_audit_writer_role_integration.py",
)

_ROOTS: Final[tuple[str, ...]] = (
    "packages",
    "services",
)

# Match ``INSERT INTO [<schema>.]audit_log`` with arbitrary whitespace
# between tokens. The ``(?i)`` flag is case-insensitive on the SQL
# keywords; the table name itself must be ``audit_log`` (snake_case)
# to keep noise out.
_RE_INSERT = re.compile(
    r"insert\s+into\s+(?:[a-z_][a-z0-9_]*\.)?audit_log\b",
    re.IGNORECASE,
)


def _is_allowed(path: Path, repo_root: Path) -> bool:
    rel = path.relative_to(repo_root).as_posix()
    return any(rel == suffix or rel.endswith("/" + suffix) for suffix in _ALLOWLIST_SUFFIXES)


def _iter_files(root: Path) -> Iterator[Path]:
    if not root.exists():
        return
    for pattern in ("*.py", "*.sql"):
        for path in root.rglob(pattern):
            if "__pycache__" in path.parts:
                continue
            yield path


def _scan(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, matched_line)`` pairs for INSERT-INTO-audit_log hits."""
    findings: list[tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return findings

    for lineno, line in enumerate(text.splitlines(), start=1):
        if _RE_INSERT.search(line):
            findings.append((lineno, line.strip()))
    return findings


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    failed: list[tuple[Path, int, str]] = []

    for root_name in _ROOTS:
        for path in _iter_files(repo_root / root_name):
            if _is_allowed(path, repo_root):
                continue
            for lineno, line in _scan(path):
                failed.append((path, lineno, line))

    if not failed:
        print("OK — INSERT INTO audit_log confined to the audit writer path")
        return 0

    print(
        f"FAIL — {len(failed)} INSERT INTO audit_log outside the allowed writer path. "
        f"Route writes through SqlAuditLogStore.append (which SETs role 'audit_writer').",
        file=sys.stderr,
    )
    for path, lineno, line in failed:
        rel = path.relative_to(repo_root)
        print(f"  {rel}:{lineno}  {line}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
