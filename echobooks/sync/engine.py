"""The sync orchestration: push local changes, pull remote, merge by LWW.

This module is transport-agnostic — it talks to anything implementing
:class:`SyncTransport` (the real one is :class:`echobooks.sync.client.SyncClient`,
and tests pass a fake). It owns the *order* of operations and the bookkeeping of
``last_sync``; the wire format and merge live in :mod:`echobooks.sync.serialize`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, NamedTuple, Protocol

from sqlalchemy.orm import sessionmaker

from echobooks.sync.serialize import (
    SyncPayload,
    apply_remote,
    dump_books,
    dump_dirty,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class SyncTransport(Protocol):
    """What the engine needs from a server connection."""

    async def push(self, payload: SyncPayload) -> None: ...

    async def pull(self, since: str | None) -> SyncPayload: ...


class SyncResult(NamedTuple):
    pushed: int
    pulled: int
    applied: int
    at: str  # ISO timestamp this sync completed


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def sync(
    session_factory: sessionmaker[Session],
    transport: SyncTransport,
    *,
    since: str | None,
) -> SyncResult:
    """Run one full sync cycle and return what moved.

    1. Push every locally-dirty row.
    2. Pull everything the server changed since ``since``.
    3. Merge pulled rows (last-write-wins) and clear their dirty flags.

    DB reads/writes happen in short sessions; the network calls happen between
    them so a session is never held open across ``await``.
    """
    # 1. Collect + push dirty rows.
    with session_factory() as session:
        outgoing = dump_dirty(session)
    await transport.push(outgoing)
    # Pushed rows are now authoritative on the server; clear their dirty flags.
    if outgoing.rows:
        pushed_ids = {(r.table, r.id) for r in outgoing.rows}
        with session_factory() as session:
            _clear_dirty(session, pushed_ids)
            session.commit()

    # 2. Pull remote changes.
    incoming = await transport.pull(since)

    # 3. Merge.
    with session_factory() as session:
        applied = apply_remote(session, incoming)
        session.commit()

    return SyncResult(
        pushed=len(outgoing.rows),
        pulled=len(incoming.rows),
        applied=applied,
        at=_now_iso(),
    )


async def import_local(
    session_factory: sessionmaker[Session],
    transport: SyncTransport,
    book_ids: list[str],
) -> int:
    """Push a chosen set of local books (and their refs) up to the account.

    Used by the import picker on first login / on a second machine. Returns the
    number of rows uploaded. Does not pull — the caller follows with a full
    :func:`sync` to bring the merged account state back down.
    """
    with session_factory() as session:
        payload = dump_books(session, book_ids)
    await transport.push(payload)
    if payload.rows:
        pushed_ids = {(r.table, r.id) for r in payload.rows}
        with session_factory() as session:
            _clear_dirty(session, pushed_ids)
            session.commit()
    return len(payload.rows)


def _clear_dirty(session: Session, ids: set[tuple[str, str]]) -> None:
    """Mark pushed rows clean *without* bumping ``updated_at``.

    A plain attribute set would fire the ``onupdate=_now`` hook on
    ``updated_at`` and make the row look freshly edited — it would re-push next
    cycle and could even win LWW against the server copy we just sent. A bulk
    UPDATE of only the ``dirty`` column sidesteps the ORM's onupdate entirely.
    """
    from sqlalchemy import update

    from echobooks.sync.serialize import ENTITY_MODELS

    by_table: dict[str, set[str]] = {}
    for table, row_id in ids:
        by_table.setdefault(table, set()).add(row_id)
    for table, row_ids in by_table.items():
        model = ENTITY_MODELS[table]
        session.execute(update(model).where(model.id.in_(row_ids)).values(dirty=False))


__all__ = ["SyncResult", "SyncTransport", "import_local", "sync"]
