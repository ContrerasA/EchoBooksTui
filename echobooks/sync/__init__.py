"""Client-side sync: wire format, last-write-wins merge, and the HTTP client.

Depends only on the client's existing dependencies (httpx, pydantic,
sqlalchemy). It must never import :mod:`echobooks.server` — the offline client
ships without the server's dependencies.
"""

from echobooks.sync.engine import SyncResult, import_local, sync
from echobooks.sync.serialize import SyncPayload, apply_remote, dump_dirty

__all__ = [
    "SyncPayload",
    "SyncResult",
    "apply_remote",
    "dump_dirty",
    "import_local",
    "sync",
]
