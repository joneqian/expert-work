"""Embedding vector dimension — Stream J.3 (long-term memory).

The dimension follows the deployment's embedding provider — qwen
``text-embedding-v4`` is 1024, other vendors differ — so it is *not*
hardcoded. It is fixed **per deployment**: the ``memory_item.embedding``
column is created with this dimension at migration time, so changing it
later requires a migration. Override with ``HELIX_AGENT_EMBEDDING_DIM``.
"""

from __future__ import annotations

import os
from typing import Final

#: Default 1024 — qwen ``text-embedding-v4``.
EMBEDDING_DIM: Final[int] = int(os.environ.get("HELIX_AGENT_EMBEDDING_DIM", "1024"))
