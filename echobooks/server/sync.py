"""Per-user sync endpoints: ``/sync/push`` and ``/sync/pull``.

Both reuse the shared wire format and merge logic (:mod:`echobooks.sync.serialize`)
— there is no separate server schema. Push applies last-write-wins and stamps every
row with the authenticated user's id; pull returns that user's rows changed since a
timestamp (tombstones included) so the client can merge incrementally.

Isolation is enforced on every query by filtering ``user_id == current_user.id``:
a user can never read or overwrite another user's rows even if they send a row id
that exists under a different owner.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from echobooks.server.auth import CurrentUser
from echobooks.server.db import get_session
from echobooks.sync.serialize import (
    ENTITY_MODELS,
    EntityWire,
    SyncPayload,
    as_naive_utc,
    entity_to_wire,
    merge_payload,
)

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/push", response_model=SyncPayload)
def push(
    payload: SyncPayload,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> SyncPayload:
    """Apply the client's rows under this user (LWW) and echo back the winners."""
    winners = merge_payload(session, payload, owner_id=user.id, scope_to_owner=True)
    return SyncPayload(rows=winners)


@router.get("/pull", response_model=SyncPayload)
def pull(
    user: CurrentUser,
    since: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> SyncPayload:
    """Return this user's rows updated since ``since`` (ISO), tombstones included."""
    cutoff: datetime | None = None
    if since:
        cutoff = as_naive_utc(datetime.fromisoformat(since))

    rows: list[EntityWire] = []
    for table, model in ENTITY_MODELS.items():
        stmt = select(model).where(model.user_id == user.id)
        if cutoff is not None:
            stmt = stmt.where(model.updated_at > cutoff)
        for obj in session.scalars(stmt):
            rows.append(entity_to_wire(table, obj))
    return SyncPayload(rows=rows)
