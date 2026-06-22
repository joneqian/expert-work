"""Sandbox-image site hook — make stdlib ``urllib`` authenticate to the
transparent egress proxy on HTTPS ``CONNECT`` (sandbox-egress §3.5).

Why this exists
---------------
The supervisor enables per-agent egress by injecting ``HTTPS_PROXY`` plus a
per-sandbox token (sandbox-egress §3.2). ``requests``/``httpx``/``urllib3`` send
that token as ``Proxy-Authorization`` on the ``CONNECT`` automatically — but
stdlib ``urllib.request`` does **not**: its HTTPS-over-proxy ``CONNECT`` carries
only ``Host:``, so the token is dropped and the proxy answers ``407``. A live
e2e (``tools/eval/verify_live_egress.py``) caught this; CI cannot, since it never
makes a real ``CONNECT``.

What it does
------------
When the supervisor-injected ``HELIX_EGRESS_PROXY_AUTH`` env is present (the
base64 of ``"<token>:"`` — exactly what a ``Basic`` proxy-auth header carries),
patch ``http.client.HTTPConnection.set_tunnel`` to add ``Proxy-Authorization``
to every ``CONNECT`` that does not already set it. urllib routes proxied HTTPS
through ``set_tunnel``, so this transparently fixes it. Clients that already send
the header keep theirs (``setdefault``), so ``requests``/``httpx`` are untouched.

Loading
-------
Python auto-imports ``sitecustomize`` from the global site-packages at startup.
The sandbox runner runs submitted code via ``python -I -c`` (isolated mode):
``-I`` is ``-E -s`` — it ignores ``PYTHON*`` env and the *user* site dir, but it
is **not** ``-S``, so the ``site`` module still initializes and still imports this
module from the global site-packages. ``-E`` only suppresses ``PYTHON*`` config
env, so ``HELIX_EGRESS_PROXY_AUTH`` is still readable.
"""

from __future__ import annotations

import http.client
import os

_AUTH = os.environ.get("HELIX_EGRESS_PROXY_AUTH")

if _AUTH:
    _PROXY_AUTH_HEADER = f"Basic {_AUTH}"
    _orig_set_tunnel = http.client.HTTPConnection.set_tunnel

    def _set_tunnel(self, host, port=None, headers=None, **kwargs):  # type: ignore[no-untyped-def]
        merged = dict(headers) if headers else {}
        # Only fill what the client did not already provide — never override a
        # client's own proxy auth.
        if not any(k.lower() == "proxy-authorization" for k in merged):
            merged["Proxy-Authorization"] = _PROXY_AUTH_HEADER
        return _orig_set_tunnel(self, host, port=port, headers=merged, **kwargs)

    http.client.HTTPConnection.set_tunnel = _set_tunnel  # type: ignore[method-assign]
