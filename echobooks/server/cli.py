"""Console entry point: ``echobooks-server`` (runs the API under uvicorn)."""

from __future__ import annotations

from echobooks.server.config import get_settings


def main() -> None:
    import uvicorn

    settings = get_settings()
    if not settings.configured:
        print(
            "Warning: GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / JWT_SECRET are not set. "
            "Login will return 503 until they are configured (see README)."
        )
    uvicorn.run(
        "echobooks.server.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
