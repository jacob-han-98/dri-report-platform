"""App settings loaded from a TOML file (HYBE_REPORTS_CONFIG) plus env overrides."""
from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field


class AppSection(BaseModel):
    base_url: str = "http://localhost:8000"
    secret_key: str = "dev-only-not-secret-change-me"
    session_lifetime_days: int = 7
    # Subpath the app is mounted under behind a reverse proxy. "" for root.
    # Examples: "" (dev with Caddy), "/dri_report" (prod under nginx).
    # Affects: FastAPI root_path, cookie path, OAuth callback URL,
    # and absolute URLs rendered in templates (via the `url()` Jinja helper).
    web_prefix: str = ""


class GoogleSection(BaseModel):
    client_id: str = ""
    client_secret: str = ""
    allowed_domain: str = ""
    admin_emails: list[str] = Field(default_factory=list)


class StorageSection(BaseModel):
    reports_dir: str = "./var/reports"
    db_path: str = "./var/reports/_meta/reports.db"
    max_upload_mb: int = 100
    max_extracted_mb: int = 200
    max_files_per_report: int = 5000


class TokenSection(BaseModel):
    default_expiry_days: int = 90
    max_expiry_days: int = 365
    prefix: str = "hybe_pat_"


class DevSection(BaseModel):
    bypass_auth_email: str = ""
    bypass_auth_role: str = "admin"


class AuditSection(BaseModel):
    retention_days: int = 365


class Settings(BaseModel):
    app: AppSection = Field(default_factory=AppSection)
    google: GoogleSection = Field(default_factory=GoogleSection)
    storage: StorageSection = Field(default_factory=StorageSection)
    token: TokenSection = Field(default_factory=TokenSection)
    dev: DevSection = Field(default_factory=DevSection)
    audit: AuditSection = Field(default_factory=AuditSection)

    @property
    def reports_dir_path(self) -> Path:
        return Path(self.storage.reports_dir).resolve()

    @property
    def db_path(self) -> Path:
        return Path(self.storage.db_path).resolve()


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    config_path = Path(os.environ.get("HYBE_REPORTS_CONFIG", "config.toml"))
    data = _load_toml(config_path)
    settings = Settings.model_validate(data)
    settings.reports_dir_path.mkdir(parents=True, exist_ok=True)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    return settings
