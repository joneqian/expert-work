"""Workspace volume layout conventions — Stream J.15.

A user's ``/workspace`` mixes three kinds of content in one flat volume:

* **agent output** — whatever the agent wrote (a generated PDF, ``out/…``);
  this is what a user actually wants to retrieve.
* **machinery** — activated skill packages the runtime seeds before exec
  (skill-runtime §5.1), materialised under ``skills/<name>/…``.
* **inputs** — documents the user uploaded for a later run's ``read_document``,
  landing under ``uploads/…``.

The machinery + input namespaces are *system-reserved*: the platform controls
exactly where they go, so they can be enumerated. Agent output, by contrast,
can be written anywhere and cannot be allow-listed. The browse / download
surface therefore **hides the reserved prefixes and shows everything else** —
the same model as ``.gitignore`` hiding generated dirs.

This module is the single source of truth for those prefixes: the seeders that
*write* them and the browser that *hides* them both import from here, so a path
change in one place can never silently desync from the filter.
"""

from __future__ import annotations

#: Activated skill packages, seeded at ``skills/<name>/…`` (skill-runtime §5.1).
WORKSPACE_SKILLS_DIR = "skills"

#: User-uploaded documents, landing at ``uploads/<name>`` for ``read_document``.
WORKSPACE_UPLOADS_DIR = "uploads"

#: Top-level workspace prefixes that hold machinery / inputs rather than agent
#: output — hidden from the "agent products" browse view. Add a new reserved
#: namespace here (and use the matching constant where it is written) and every
#: browse surface picks it up automatically.
WORKSPACE_RESERVED_PREFIXES: frozenset[str] = frozenset(
    {WORKSPACE_SKILLS_DIR, WORKSPACE_UPLOADS_DIR}
)


def is_reserved_workspace_path(relpath: str) -> bool:
    """Return whether ``relpath`` lives under a reserved (non-output) namespace.

    Compares the first path segment against :data:`WORKSPACE_RESERVED_PREFIXES`;
    a bare top-level file (no ``/``) is never reserved.
    """
    return relpath.split("/", 1)[0] in WORKSPACE_RESERVED_PREFIXES
