"""Small in-process TTL memo for read-only analysis lookups.

The prediction bot analyses many fixtures in one batch.  The data it reads is
static between ingests, so repeated lookups -- the team-name map, a
competition's training set, one team's recent form -- are memoised briefly to
cut the database round-trips that otherwise dominate a batch.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Hashable
from typing import Any

#: How long an entry stays valid.  The web app only reads; ingests run offline
#: through the CLI, so a couple of minutes of staleness is harmless.
_TTL_SECONDS = 120.0
_MAX_ENTRIES = 8192

_store: dict[Hashable, tuple[float, Any]] = {}
_lock = threading.Lock()


def memo(key: Hashable, producer: Callable[[], Any], *, ttl: float = _TTL_SECONDS) -> Any:
    """Return ``key``'s cached value, computing it with ``producer`` on a miss."""
    now = time.monotonic()
    with _lock:
        hit = _store.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
    value = producer()
    with _lock:
        if len(_store) >= _MAX_ENTRIES:
            _store.clear()
        _store[key] = (now, value)
    return value


def clear() -> None:
    """Drop every cached entry (used when stored data changes)."""
    with _lock:
        _store.clear()
