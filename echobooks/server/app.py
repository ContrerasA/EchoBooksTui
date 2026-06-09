"""FastAPI application factory for the EchoBooks sync server."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from echobooks.server import auth, sync
from echobooks.server.db import init_db


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_db()  # ensure the schema exists on startup
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="EchoBooks Sync Server", lifespan=_lifespan)
    app.include_router(auth.router)
    app.include_router(sync.router)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


# Module-level app so `uvicorn echobooks.server.app:app` works.
app = create_app()
