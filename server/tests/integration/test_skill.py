"""hybe-reports CLI — invokes the installed `hybe-reports` entrypoint.

Token is injected via env var so we never touch the user's keyring or
~/.config file. Server must be running at https://localhost:8443.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app.auth.tokens import issue_token
from app.db import SessionLocal
from app.models import ApiToken, User

BASE_URL = os.environ.get("HYBE_E2E_BASE_URL", "https://localhost:8443")
SKILL_BIN = str(Path(__file__).resolve().parents[2] / ".venv" / "bin" / "hybe-reports")


@pytest.fixture(scope="module")
def cli_token():
    """Issue a real token directly via the DB; revoke at teardown."""
    db = SessionLocal()
    try:
        user = db.get(User, "jaekap.han@gmail.com")
        assert user is not None, "bypass user must exist; run dev server once"
        issued = issue_token(db, user=user, name="cli-e2e", expires_in_days=1)
    finally:
        db.close()
    yield issued.plaintext, issued.prefix
    # mark revoked directly — avoids the detached-User issue with revoke_token()
    db = SessionLocal()
    try:
        t = db.query(ApiToken).filter(ApiToken.prefix == issued.prefix).one_or_none()
        if t and t.revoked_at is None:
            t.revoked_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


def _run(args: list[str], token: str, **kw) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "HYBE_REPORTS_BASE_URL": BASE_URL,
        "HYBE_REPORTS_TOKEN": token,
        "HYBE_REPORTS_VERIFY": "0",  # self-signed dev TLS
    }
    return subprocess.run(
        [SKILL_BIN, *args],
        env=env, capture_output=True, text=True, timeout=30, **kw,
    )


@pytest.fixture()
def fresh_slug():
    return f"cli-e2e-{int(time.time() * 1000)}"


@pytest.fixture()
def site_dir(tmp_path: Path) -> Path:
    """Tiny static site for upload tests."""
    (tmp_path / "index.html").write_text(
        "<h1>cli e2e fixture</h1><p>UNIQUE-CLI-MARKER</p>", encoding="utf-8"
    )
    (tmp_path / "data.json").write_text('{"x": 1}', encoding="utf-8")
    # this should be excluded by the packager
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("ignored", encoding="utf-8")
    return tmp_path


def _delete_via_api(slug: str, token: str) -> None:
    httpx.delete(
        f"{BASE_URL}/api/reports/{slug}",
        headers={"Authorization": f"Bearer {token}"},
        verify=False,
    )


def test_cli_help_runs() -> None:
    # no token needed for --help
    r = subprocess.run([SKILL_BIN, "--help"], capture_output=True, text=True)
    assert r.returncode == 0
    assert "hybe-reports" in r.stdout.lower()
    for cmd in ("deploy", "list", "info", "share", "redeploy", "delete"):
        assert cmd in r.stdout


def test_cli_deploy_then_list_then_info(
    cli_token, fresh_slug: str, site_dir: Path
) -> None:
    token, _ = cli_token
    try:
        # deploy
        r = _run(
            ["deploy", str(site_dir), "--slug", fresh_slug,
             "--title", "CLI e2e", "--visibility", "internal", "--tag", "cli"],
            token=token,
        )
        assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"
        assert fresh_slug in r.stdout
        assert "/r/" in r.stdout

        # served at /r/{slug}/
        served = httpx.get(f"{BASE_URL}/r/{fresh_slug}/", verify=False)
        assert served.status_code == 200
        assert "UNIQUE-CLI-MARKER" in served.text

        # list --mine
        r = _run(["list", "--mine"], token=token)
        assert r.returncode == 0
        assert fresh_slug in r.stdout

        # info
        r = _run(["info", fresh_slug], token=token)
        assert r.returncode == 0
        assert "CLI e2e" in r.stdout
        assert "internal" in r.stdout

        # excluded files: .git/config should NOT have been packaged → still 404
        r404 = httpx.get(f"{BASE_URL}/r/{fresh_slug}/.git/config", verify=False)
        assert r404.status_code == 404
    finally:
        _delete_via_api(fresh_slug, token)


def test_cli_share_visibility_and_viewer(
    cli_token, fresh_slug: str, site_dir: Path
) -> None:
    token, _ = cli_token
    try:
        _run(
            ["deploy", str(site_dir), "--slug", fresh_slug,
             "--title", "share-test", "--visibility", "internal"],
            token=token,
        )

        # change visibility
        r = _run(["share", fresh_slug, "--visibility", "restricted"], token=token)
        assert r.returncode == 0, r.stderr
        assert "restricted" in r.stdout

        # add viewer
        r = _run(["share", fresh_slug, "--add", "viewer-test@hybecorp.com"], token=token)
        assert r.returncode == 0, r.stderr

        # confirm via info
        r = _run(["info", fresh_slug], token=token)
        assert "viewer-test@hybecorp.com" in r.stdout

        # remove viewer
        r = _run(["share", fresh_slug, "--remove", "viewer-test@hybecorp.com"], token=token)
        assert r.returncode == 0, r.stderr
    finally:
        _delete_via_api(fresh_slug, token)


def test_cli_redeploy_replaces_files(
    cli_token, fresh_slug: str, site_dir: Path, tmp_path: Path
) -> None:
    token, _ = cli_token
    try:
        _run(
            ["deploy", str(site_dir), "--slug", fresh_slug,
             "--title", "redeploy-test", "--visibility", "internal"],
            token=token,
        )

        # build a fresh dir with different content
        new_dir = tmp_path / "v2"
        new_dir.mkdir()
        (new_dir / "index.html").write_text("<h1>VERSION-2</h1>", encoding="utf-8")

        r = _run(["redeploy", fresh_slug, str(new_dir)], token=token)
        assert r.returncode == 0, r.stderr

        served = httpx.get(f"{BASE_URL}/r/{fresh_slug}/", verify=False)
        assert "VERSION-2" in served.text
        assert "UNIQUE-CLI-MARKER" not in served.text
    finally:
        _delete_via_api(fresh_slug, token)


def test_cli_delete_removes_report(
    cli_token, fresh_slug: str, site_dir: Path
) -> None:
    token, _ = cli_token
    _run(
        ["deploy", str(site_dir), "--slug", fresh_slug,
         "--title", "delete-test", "--visibility", "internal"],
        token=token,
    )
    r = _run(["delete", fresh_slug, "--yes"], token=token)
    assert r.returncode == 0, r.stderr

    # gone from /r/
    served = httpx.get(f"{BASE_URL}/r/{fresh_slug}/", verify=False)
    assert served.status_code == 404


def test_cli_invalid_token_friendly_error(fresh_slug: str, site_dir: Path) -> None:
    r = _run(
        ["deploy", str(site_dir), "--slug", fresh_slug, "--title", "fail",
         "--visibility", "internal"],
        token="hybe_pat_definitelynotreal",
    )
    assert r.returncode != 0
    # Korean help text from client._humanize
    assert "토큰이 유효하지 않" in r.stderr or "401" in r.stderr


def test_cli_slug_collision_friendly_error(
    cli_token, fresh_slug: str, site_dir: Path
) -> None:
    token, _ = cli_token
    try:
        _run(
            ["deploy", str(site_dir), "--slug", fresh_slug,
             "--title", "collide", "--visibility", "internal"],
            token=token,
        )
        r = _run(
            ["deploy", str(site_dir), "--slug", fresh_slug,
             "--title", "collide-2", "--visibility", "internal"],
            token=token,
        )
        assert r.returncode != 0
        assert "slug 충돌" in r.stderr or "409" in r.stderr
    finally:
        _delete_via_api(fresh_slug, token)
