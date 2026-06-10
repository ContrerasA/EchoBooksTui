"""Tiny, dialect-agnostic schema helpers shared by the client and the server.

``create_all`` only creates missing *tables*, never missing *columns*, so a
table that predates a new field needs an explicit ``ALTER TABLE … ADD COLUMN``.
This lives in its own module (no app config imports) so the sync server can use
it without pulling in client-only configuration.
"""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text


def add_missing_columns(engine: Engine, table: str, columns: dict[str, str]) -> None:
    """Add any of ``columns`` (``name -> SQL type``) absent from ``table``.

    A no-op when the table doesn't exist yet (``create_all`` will build it with
    the columns already present). Types must be portable across the engines we
    target — SQLite (client) and Postgres (server) — e.g. ``FLOAT``, ``TEXT``,
    ``VARCHAR(32)``. The columns are nullable, so a plain add is safe on both.
    """
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns(table)}
    with engine.begin() as conn:
        for name, sql_type in columns.items():
            if name not in existing:
                conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {name} {sql_type}'))
