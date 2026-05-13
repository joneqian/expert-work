"""CI lint: enforce a single canonical RLS GUC variable name.

Per STREAM-C-DESIGN § 2.4 and subsystems/23 § 8 the application uses
exactly one Postgres session variable to carry the active tenant id:

    app.tenant_id

Two failure modes this script catches:

1.  An alembic migration that uses a typo'd or stale variant such as
    ``app.tenant``, ``current_tenant``, or ``tenant_id`` (without the
    ``app.`` prefix). The migration applies cleanly, the RLS policy
    looks correct in isolation, but no row ever matches at runtime
    because the application-side ``set_config`` writes a different
    name.

2.  An application file that calls ``SET LOCAL`` or ``set_config``
    with a name other than ``app.tenant_id``. Reverse of the above —
    application writes the right value, but the policy never sees it.

The lint inspects ``packages/helix-persistence/migrations/`` plus all
``*.py`` under ``packages`` and ``services`` for the strings
``current_setting``, ``set_config(``, and ``SET LOCAL`` (case-insensitive
on the SQL keywords). For each match it extracts the variable name and
ensures it equals :data:`CANONICAL_NAME`.

Run via ``python tools/persistence/check_rls_naming.py`` from the
repo root. Exits 0 when clean, 1 otherwise.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Final

CANONICAL_NAME: Final[str] = "app.tenant_id"

# Roots scanned. Lint is repo-relative so CI / dev / pre-commit all run
# the same script with no extra args.
_ROOTS: Final[tuple[str, ...]] = (
    "packages",
    "services",
)

# Files explicitly allowed to mention non-canonical names — typically
# this lint script itself plus tests that intentionally inject a bad
# name to verify the policy rejects it.
_ALLOWLIST_SUFFIXES: Final[tuple[str, ...]] = ("tools/persistence/check_rls_naming.py",)

# ``current_setting('NAME', ...)`` — single or double quote, with or
# without the second ``true`` arg.
_RE_CURRENT_SETTING = re.compile(
    r"""current_setting\s*\(\s*['"]([A-Za-z0-9_.]+)['"]""",
    re.IGNORECASE,
)

# ``set_config('NAME', ..., ...)`` — three-arg form.
_RE_SET_CONFIG = re.compile(
    r"""set_config\s*\(\s*['"]([A-Za-z0-9_.]+)['"]""",
    re.IGNORECASE,
)

# ``SET LOCAL <name> = ...`` (SQL keyword form). Used in older
# documentation; not the form we emit, but if someone writes it we
# still want the canonical name.
_RE_SET_LOCAL = re.compile(
    r"""SET\s+LOCAL\s+([A-Za-z0-9_.]+)\s*=""",
    re.IGNORECASE,
)


def _is_allowed(path: Path) -> bool:
    posix = path.as_posix()
    return any(posix.endswith(suffix) for suffix in _ALLOWLIST_SUFFIXES)


def _iter_files(root: Path) -> Iterator[Path]:
    if not root.exists():
        return
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _scan(path: Path) -> list[tuple[int, str, str]]:
    """Return ``(lineno, kind, found_name)`` triples for non-canonical hits."""
    findings: list[tuple[int, str, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return findings

    for lineno, line in enumerate(text.splitlines(), start=1):
        for kind, regex in (
            ("current_setting", _RE_CURRENT_SETTING),
            ("set_config", _RE_SET_CONFIG),
            ("SET LOCAL", _RE_SET_LOCAL),
        ):
            for match in regex.finditer(line):
                name = match.group(1)
                # Skip the canonical name itself.
                if name == CANONICAL_NAME:
                    continue
                # Skip obviously unrelated GUCs (``row_security``,
                # ``statement_timeout``, ``timezone`` …). The lint
                # only cares about tenant-shaped names.
                if "tenant" not in name.lower():
                    continue
                findings.append((lineno, kind, name))
    return findings


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    failed: list[tuple[Path, int, str, str]] = []

    for root_name in _ROOTS:
        for path in _iter_files(repo_root / root_name):
            if _is_allowed(path):
                continue
            for lineno, kind, name in _scan(path):
                failed.append((path, lineno, kind, name))

    if not failed:
        print(f"OK — all RLS references use canonical name {CANONICAL_NAME!r}")
        return 0

    print(
        f"FAIL — {len(failed)} non-canonical RLS reference(s); expected {CANONICAL_NAME!r}",
        file=sys.stderr,
    )
    for path, lineno, kind, name in failed:
        rel = path.relative_to(repo_root)
        print(f"  {rel}:{lineno}  [{kind}]  {name!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
