"""Minimal-image smoke payload — stdlib only (Stream HX-10 PR3).

Runs inside the built minimal sandbox image via the runner's JSON protocol.
The minimal image is pure-stdlib (Mini-ADR F-2/F-13, no package-install
path), so this only exercises the interpreter + a couple of stdlib modules
and prints ``OK``. The host-side driver (smoke_test.py) asserts on that.
"""

import json
import sys

# Touch a few stdlib modules the runner itself doesn't import, so a broken
# base image (missing stdlib, wrong Python) fails loudly here.
if sys.version_info[:2] != (3, 12):
    raise SystemExit(f"unexpected Python: {sys.version}")
json.dumps({"probe": [1, 2, 3]})

print("OK")
