"""Token storage: keyring path, JSON fallback, and the one-time migration."""

from __future__ import annotations

import json

import keyring
import pytest
from keyring.backend import KeyringBackend

from echobooks import keyring_store
from echobooks.config import Settings


class _MemKeyring(KeyringBackend):
    """An in-memory keyring backend so tests never touch the real OS store."""

    priority = 1

    def __init__(self) -> None:
        self._d: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, user: str) -> str | None:
        return self._d.get((service, user))

    def set_password(self, service: str, user: str, password: str) -> None:
        self._d[(service, user)] = password

    def delete_password(self, service: str, user: str) -> None:
        self._d.pop((service, user), None)


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHOBOOKS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("ECHOBOOKS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ECHOBOOKS_NO_KEYRING", raising=False)
    return tmp_path


@pytest.fixture
def mem_keyring():
    """Activate a fresh in-memory keyring backend for the test.

    Must use ``keyring.set_keyring`` (not monkeypatching ``get_keyring``):
    ``keyring.set_password`` resolves the backend through the library's own
    internal reference, so patching the ``get_keyring`` *name* does NOT redirect
    writes — they would leak to the developer's real OS keyring. ``set_keyring``
    swaps the actual backend; we restore the original afterward.
    """
    original = keyring.get_keyring()
    backend = _MemKeyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(original)


def _json(tmp_path) -> dict:
    return json.loads((tmp_path / "settings.json").read_text())


# --------------------------------------------------------------------------- #
# Keyring present
# --------------------------------------------------------------------------- #
def test_tokens_go_to_keyring_not_json(config_dir, mem_keyring):
    assert keyring_store.available()
    s = Settings()
    s.server_url = "https://srv.test"
    s.access_token = "ACCESS"
    s.refresh_token = "REFRESH"
    s.save()

    raw = _json(config_dir)
    assert "access_token" not in raw  # stripped from JSON
    assert "refresh_token" not in raw
    assert keyring_store.get(keyring_store.ACCESS_KEY) == "ACCESS"

    reloaded = Settings.load()
    assert reloaded.access_token == "ACCESS"
    assert reloaded.refresh_token == "REFRESH"
    assert reloaded.is_logged_in()


def test_clear_account_wipes_keyring(config_dir, mem_keyring):
    s = Settings()
    s.server_url = "https://srv.test"
    s.access_token = "ACCESS"
    s.refresh_token = "REFRESH"
    s.save()
    s.clear_account()

    assert keyring_store.get(keyring_store.ACCESS_KEY) in (None, "")
    assert not Settings.load().is_logged_in()


# --------------------------------------------------------------------------- #
# One-time migration of old plaintext-JSON tokens
# --------------------------------------------------------------------------- #
def test_migrates_plaintext_json_into_keyring(config_dir, mem_keyring):
    # Simulate an older install that wrote tokens straight into settings.json.
    (config_dir / "settings.json").write_text(
        json.dumps(
            {
                "server_url": "https://srv.test",
                "access_token": "OLD-ACCESS",
                "refresh_token": "OLD-REFRESH",
                "user_email": "me@example.com",
            }
        )
    )

    loaded = Settings.load()  # triggers migration
    assert loaded.access_token == "OLD-ACCESS"

    raw = _json(config_dir)
    assert "access_token" not in raw  # scrubbed from disk
    assert keyring_store.get(keyring_store.ACCESS_KEY) == "OLD-ACCESS"


# --------------------------------------------------------------------------- #
# Fallback: no keyring backend
# --------------------------------------------------------------------------- #
def test_fallback_keeps_tokens_in_json(config_dir, monkeypatch):
    monkeypatch.setenv("ECHOBOOKS_NO_KEYRING", "1")
    assert not keyring_store.available()

    s = Settings()
    s.server_url = "https://srv.test"
    s.access_token = "ACCESS"
    s.save()

    raw = _json(config_dir)
    assert raw["access_token"] == "ACCESS"  # falls back to JSON
    assert Settings.load().access_token == "ACCESS"
