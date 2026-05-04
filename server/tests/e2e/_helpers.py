"""Test helpers — fresh-slug fixtures, API uploads via httpx."""
from __future__ import annotations

import io
import json
import time
import zipfile
from typing import Any

import httpx


def form_post(
    base_url: str, path: str, data: dict, files: dict | None = None
) -> httpx.Response:
    """POST to a form endpoint with auto-fetched CSRF token.

    Mimics what a browser does: GET something to seed `hybe_csrf` cookie,
    then submit the form with both cookie and matching csrf_token field.
    """
    with httpx.Client(verify=False, follow_redirects=False) as c:
        c.get(f"{base_url}/")  # seed CSRF cookie
        token = c.cookies.get("hybe_csrf", "")
        merged: dict[str, Any] = {**data, "csrf_token": token}
        if files:
            return c.post(f"{base_url}{path}", data=merged, files=files)
        return c.post(f"{base_url}{path}", data=merged)


def _make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def upload_report(
    base_url: str,
    *,
    slug: str,
    title: str,
    visibility: str = "restricted",
    description: str | None = None,
    tags: list[str] | None = None,
    html_body: str = "<h1>Test</h1>",
) -> dict:
    payload = _make_zip({"index.html": html_body})
    meta: dict = {"slug": slug, "title": title, "visibility": visibility}
    if description:
        meta["description"] = description
    if tags:
        meta["tags"] = tags
    files = {"file": (f"{slug}.zip", payload, "application/zip")}
    data = {"meta": json.dumps(meta)}
    resp = httpx.post(
        f"{base_url}/api/reports", files=files, data=data, verify=False, timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def delete_report(base_url: str, slug: str) -> None:
    httpx.delete(f"{base_url}/api/reports/{slug}", verify=False, timeout=30)


def fresh_slug(prefix: str = "e2e") -> str:
    return f"{prefix}-{int(time.time() * 1000)}"
