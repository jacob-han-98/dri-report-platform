"""Token + base_url storage. Tries OS keyring first, falls back to ~/.config/hybe-reports/config.json."""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

KEYRING_SERVICE = "hybe-reports"
KEYRING_USER = "default"  # one logical session per user; supports profiles later
CONFIG_PATH = Path.home() / ".config" / "hybe-reports" / "config.json"


@dataclass
class Config:
    base_url: str
    token: str

    @property
    def verify_tls(self) -> bool:
        v = os.environ.get("HYBE_REPORTS_VERIFY", "1").lower()
        return v not in ("0", "false", "no", "off")


def _try_keyring_get() -> dict | None:
    try:
        import keyring  # type: ignore
    except Exception:
        return None
    try:
        raw = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _try_keyring_set(data: dict) -> bool:
    try:
        import keyring  # type: ignore
    except Exception:
        return False
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, json.dumps(data))
        return True
    except Exception:
        return False


def _file_get() -> dict | None:
    if not CONFIG_PATH.exists():
        return None
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _file_set(data: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        CONFIG_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def load() -> Config | None:
    """Resolve config in order: env > keyring > file. Returns None if no token at all."""
    env_token = os.environ.get("HYBE_REPORTS_TOKEN")
    env_base = os.environ.get("HYBE_REPORTS_BASE_URL")

    stored = _try_keyring_get() or _file_get() or {}

    base_url = env_base or stored.get("base_url")
    token = env_token or stored.get("token")

    if not base_url or not token:
        return None
    return Config(base_url=base_url.rstrip("/"), token=token)


def save(base_url: str, token: str) -> str:
    """Persist credentials. Returns 'keyring' or 'file' to indicate where it was stored."""
    data = {"base_url": base_url.rstrip("/"), "token": token}
    if _try_keyring_set(data):
        return "keyring"
    _file_set(data)
    return "file"


def clear() -> None:
    try:
        import keyring  # type: ignore

        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
        except Exception:
            pass
    except Exception:
        pass
    if CONFIG_PATH.exists():
        try:
            CONFIG_PATH.unlink()
        except OSError:
            pass
