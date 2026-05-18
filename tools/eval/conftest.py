"""Put this directory on ``sys.path`` so the tests can ``import helix_eval``.

``tools/eval`` is a dev tool, not an installed workspace package — the
harness module lives next to its tests.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
