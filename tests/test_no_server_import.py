"""Guard the offline-client-has-zero-server-deps constraint.

The client (TUI + sync engine) must import and run with the server's heavy
dependencies absent. This test simulates a client-only install by blocking
``fastapi`` (and friends) from importing, then imports every client entry point.
If anything under ``echobooks`` outside ``echobooks.server`` ever imports the
server package — directly or transitively — this test fails.
"""

from __future__ import annotations

import builtins
import importlib

import pytest

# Modules that only exist in a `echobooks[server]` install.
_SERVER_ONLY = ("fastapi", "uvicorn", "authlib", "jwt", "asyncpg", "psycopg")

# The client surface that must import without any server dependency.
_CLIENT_MODULES = [
    "echobooks.app",
    "echobooks.__main__",
    "echobooks.config",
    "echobooks.db.session",
    "echobooks.db.repository",
    "echobooks.sync",
    "echobooks.sync.engine",
    "echobooks.sync.serialize",
    "echobooks.sync.client",
]


@pytest.fixture
def no_server_deps(monkeypatch):
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        root = name.split(".")[0]
        if root in _SERVER_ONLY:
            raise ModuleNotFoundError(f"blocked server dep: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    yield


def test_client_imports_without_server_deps(no_server_deps):
    for mod in _CLIENT_MODULES:
        # Force a fresh import so the guard is exercised, not a cached module.
        import sys

        sys.modules.pop(mod, None)
        importlib.import_module(mod)
