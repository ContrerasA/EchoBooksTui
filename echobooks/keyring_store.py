"""Secure storage for account tokens, with a graceful fallback.

Account access/refresh tokens are secrets — anyone who reads them can act as the
signed-in user until they expire. We keep them in the OS keyring (Keychain on
macOS, Secret Service / libsecret on Linux, Credential Manager on Windows) so
they're encrypted at rest instead of sitting in plaintext ``settings.json``.

Not every machine has a working keyring (headless servers, minimal containers,
CI). When none is available — or ``ECHOBOOKS_NO_KEYRING`` is set — callers fall
back to JSON storage so login still works everywhere. This module only reports
whether the keyring is usable and does the get/set/delete; the fallback policy
lives in :class:`echobooks.config.Settings`.
"""

from __future__ import annotations

import os

# The keyring service name groups our entries in the OS store.
SERVICE = "echobooks"
# The two secrets we persist, keyed by username within the service.
ACCESS_KEY = "access_token"
REFRESH_KEY = "refresh_token"


def _disabled_by_env() -> bool:
    return os.environ.get("ECHOBOOKS_NO_KEYRING", "").strip().lower() in {"1", "true", "yes"}


def available() -> bool:
    """True if a real, writable keyring backend is present (not the fail backend)."""
    if _disabled_by_env():
        return False
    try:
        import keyring
        from keyring.backends import fail

        return not isinstance(keyring.get_keyring(), fail.Keyring)
    except Exception:
        return False


def get(key: str) -> str | None:
    """Read a secret; returns None on any backend error or if absent."""
    try:
        import keyring

        return keyring.get_password(SERVICE, key)
    except Exception:
        return None


def set(key: str, value: str) -> bool:
    """Store a secret. Empty value deletes it. Returns True on success."""
    if not value:
        return delete(key)
    try:
        import keyring

        keyring.set_password(SERVICE, key, value)
        return True
    except Exception:
        return False


def delete(key: str) -> bool:
    """Remove a secret if present. Returns True if the store is now clear of it."""
    try:
        import keyring
        import keyring.errors

        try:
            keyring.delete_password(SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            pass  # already absent — that's fine
        return True
    except Exception:
        return False
