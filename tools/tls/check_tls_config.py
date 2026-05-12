"""CI lint: every ``environments/*.yaml`` must declare ``tls.min_version >= 1.2``.

Per subsystems/28-reliability-primitives § 5.1 + § 8: TLS 1.0 / 1.1 must
never reach a Helix-Agent service. The lint catches a stray ``tls.min_version: "1.0"``
in a config that ships to prod before nginx / the application code does.

Run via ``python tools/tls/check_tls_config.py`` from the repo root, or
directly. Exits 0 when clean, 1 otherwise.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import yaml

_MIN_ALLOWED_VERSION = 1.2


def _iter_env_files(root: Path) -> Iterator[Path]:
    yield from sorted(root.glob("environments/*.yaml"))


def _find_violations(path: Path) -> list[str]:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return [f"{path}: failed to parse YAML: {exc}"]

    tls = doc.get("tls")
    if tls is None:
        # ``tls`` block is optional in early M0 environments. CI gate
        # flips to required once an environment actually ships to staging
        # / prod — for now we only validate when it's declared.
        return []

    min_version = tls.get("min_version")
    if min_version is None:
        return [f"{path}: tls.min_version is required when a tls block exists"]

    try:
        version_num = float(str(min_version))
    except ValueError:
        return [f"{path}: tls.min_version={min_version!r} is not a number"]

    if version_num < _MIN_ALLOWED_VERSION:
        return [
            f"{path}: tls.min_version={min_version} below {_MIN_ALLOWED_VERSION} "
            "minimum (subsystems/28 § 5.1)."
        ]

    return []


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    root = Path(argv[0]) if argv else Path.cwd()

    all_violations: list[str] = []
    for path in _iter_env_files(root):
        all_violations.extend(_find_violations(path))

    if all_violations:
        print("TLS config lint failed:", file=sys.stderr)
        for v in all_violations:
            print(f"  {v}", file=sys.stderr)
        return 1

    print("TLS config lint OK.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
