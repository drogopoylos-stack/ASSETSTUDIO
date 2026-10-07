"""API-key storage. Prefers the OS keychain (via ``keyring``); falls back to an
obfuscated local file if no backend is available (headless servers, some CIs).

Keys are referenced by a stable ``key_name`` (e.g. ``"tripo"``, ``"openai"``)
which providers declare in their :class:`ProviderInfo.key_name`.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Optional

from .config import DATA_DIR

SERVICE = "AssetStudio"
_FALLBACK = DATA_DIR / ".keys.json"

try:  # keyring may have no usable backend in headless environments
    import keyring
    from keyring.errors import KeyringError

    _backend = keyring.get_keyring()
    _HAS_KEYRING = _backend is not None and "fail" not in type(_backend).__name__.lower()
except Exception:  # pragma: no cover - defensive
    keyring = None  # type: ignore
    KeyringError = Exception  # type: ignore
    _HAS_KEYRING = False


# --- file fallback (light obfuscation; NOT real encryption) ----------------
def _read_fallback() -> dict[str, str]:
    if not _FALLBACK.exists():
        return {}
    try:
        raw = base64.b64decode(_FALLBACK.read_bytes()).decode("utf-8")
        return json.loads(raw)
    except Exception:
        return {}


def _write_fallback(data: dict[str, str]) -> None:
    blob = base64.b64encode(json.dumps(data).encode("utf-8"))
    _FALLBACK.write_bytes(blob)
    try:
        _FALLBACK.chmod(0o600)
    except Exception:
        pass


# --- public API ------------------------------------------------------------
def set_key(key_name: str, value: str) -> None:
    if not value:
        delete_key(key_name)
        return
    if _HAS_KEYRING:
        try:
            keyring.set_password(SERVICE, key_name, value)
            return
        except KeyringError:
            pass
    data = _read_fallback()
    data[key_name] = value
    _write_fallback(data)


def get_key(key_name: str) -> Optional[str]:
    if _HAS_KEYRING:
        try:
            v = keyring.get_password(SERVICE, key_name)
            if v:
                return v
        except KeyringError:
            pass
    return _read_fallback().get(key_name)


def delete_key(key_name: str) -> None:
    if _HAS_KEYRING:
        try:
            keyring.delete_password(SERVICE, key_name)
        except Exception:
            pass
    data = _read_fallback()
    if key_name in data:
        del data[key_name]
        _write_fallback(data)


def has_key(key_name: str) -> bool:
    return bool(get_key(key_name))


def backend_name() -> str:
    return "os-keychain" if _HAS_KEYRING else "encoded-file"
