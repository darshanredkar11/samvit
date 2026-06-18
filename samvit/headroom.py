"""
Headroom compression integration — shrink tool outputs before returning to agents.

Headroom is an embedded library that applies lossless structural compression to
JSON-like tool outputs (redundant field names → short tokens, repeated values → refs).

Tools compressed: recall, search_docs, explore_code, read, list_tasks, who_calls, graph_symbol.

If headroom-ai is not installed, all operations are no-ops.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

try:
    from headroom import compress as _headroom_compress
    HAS_HEADROOM = True
except ImportError:
    HAS_HEADROOM = False

# Tools whose outputs should be compressed before returning
COMPRESSED_TOOLS = {
    "recall",
    "search_docs",
    "explore_code",
    "read",
    "list_tasks",
    "who_calls",
    "graph_symbol",
}

# Minimum response size (bytes) before we bother compressing
MIN_COMPRESS_BYTES = 1024


def compress(tool: str, result: dict) -> dict:
    """
    Wraps a tool result with a Headroom-compressed representation.
    Returns the original result unchanged if:
      - headroom-ai is not installed
      - the tool is not in COMPRESSED_TOOLS
      - the result is too small to benefit from compression
    """

    if not HAS_HEADROOM:
        return result

    if tool not in COMPRESSED_TOOLS:
        return result

    import json

    raw = json.dumps(result)
    if len(raw) < MIN_COMPRESS_BYTES:
        return result

    try:
        compressed = _headroom_compress(raw)
        log.debug("Headroom compressed %s: %d→%d bytes (%.1f%%)",
                  tool, len(raw), len(compressed),
                  (1 - len(compressed) / len(raw)) * 100 if raw else 0)
        return {
            "_compressed": True,
            "_tool": tool,
            "_original_size": len(raw),
            "_compressed_size": len(compressed),
            "data": compressed,
        }
    except Exception as exc:
        log.warning("Headroom compression failed for %s: %s", tool, exc)
        return result
