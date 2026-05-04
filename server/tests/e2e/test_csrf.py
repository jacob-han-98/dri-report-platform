"""CSRF protection — double-submit cookie + form field validation."""
from __future__ import annotations

import httpx
from playwright.sync_api import Page, expect


def test_post_without_csrf_token_returns_403(base_url: str) -> None:
    """Plain form POST without csrf_token field is rejected."""
    # cookie set but no form field
    with httpx.Client(verify=False, follow_redirects=False) as c:
        c.get(f"{base_url}/")  # seed cookie
        resp = c.post(
            f"{base_url}/settings/users/invite",
            data={"email": "should-fail@hybecorp.com", "role": "user"},
        )
    assert resp.status_code == 403
    assert "csrf" in resp.text.lower()


def test_post_with_wrong_csrf_token_returns_403(base_url: str) -> None:
    """csrf_token field mismatched against the cookie is rejected."""
    with httpx.Client(verify=False, follow_redirects=False) as c:
        c.get(f"{base_url}/")
        resp = c.post(
            f"{base_url}/settings/users/invite",
            data={
                "email": "should-fail@hybecorp.com",
                "role": "user",
                "csrf_token": "tampered-value",
            },
        )
    assert resp.status_code == 403


def test_post_with_matching_csrf_token_succeeds(base_url: str) -> None:
    """When form field matches cookie, request is allowed (303 redirect)."""
    with httpx.Client(verify=False, follow_redirects=False) as c:
        c.get(f"{base_url}/")
        token = c.cookies["hybe_csrf"]
        resp = c.post(
            f"{base_url}/settings/users/invite",
            data={
                "email": f"csrf-ok-{token[:8]}@hybecorp.com",
                "role": "user",
                "csrf_token": token,
            },
        )
    assert resp.status_code == 303


def test_bearer_auth_skips_csrf_check(base_url: str) -> None:
    """API endpoints with Bearer auth do not require csrf_token."""
    # issue a token via the API (no CSRF involved — body is JSON)
    issued = httpx.post(
        f"{base_url}/api/tokens",
        json={"name": "csrf-skip-test", "expires_in_days": 1},
        verify=False,
    ).json()
    token = issued["token"]
    prefix = issued["prefix"]

    # use Bearer to invite via /api/admin/* — no csrf_token field, must succeed
    r = httpx.post(
        f"{base_url}/api/admin/users/invite",
        json={"email": f"bearer-csrf-{prefix[-6:]}@hybecorp.com", "role": "user"},
        headers={"Authorization": f"Bearer {token}"},
        verify=False,
    )
    assert r.status_code in (200, 201), f"got {r.status_code}: {r.text}"

    # cleanup
    httpx.delete(
        f"{base_url}/api/tokens/{prefix}",
        headers={"Authorization": f"Bearer {token}"},
        verify=False,
    )


def test_browser_form_includes_csrf_input(page: Page, base_url: str) -> None:
    """Server-rendered forms have a csrf_token hidden input populated."""
    page.goto(f"{base_url}/upload")
    val = page.get_by_test_id("upload-form").locator(
        'input[name="csrf_token"]'
    ).first.get_attribute("value")
    assert val and len(val) > 16, val
