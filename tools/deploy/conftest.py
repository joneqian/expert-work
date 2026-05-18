"""Put this directory on ``sys.path`` so the tests can ``import deploy``.

``tools/deploy`` is a dev tool, not an installed workspace package — the
deploy script lives next to its tests (mirrors ``tools/eval``).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
