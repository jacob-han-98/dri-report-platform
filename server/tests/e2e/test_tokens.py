"""토큰 관리 UI — /settings/tokens 발급/표시/폐기."""
from __future__ import annotations

import httpx
import pytest
from playwright.sync_api import Page, expect

from tests.e2e._helpers import form_post


def _list_tokens_via_api(base_url: str) -> list[dict]:
    """List current user's tokens via API. dev bypass auth."""
    r = httpx.get(f"{base_url}/api/tokens", verify=False)
    r.raise_for_status()
    return r.json()


def _revoke_via_api(base_url: str, prefix: str) -> None:
    httpx.delete(f"{base_url}/api/tokens/{prefix}", verify=False)


@pytest.fixture()
def cleanup_tokens(base_url: str):
    """Revoke any tokens left over after the test."""
    before = {t["prefix"] for t in _list_tokens_via_api(base_url)}
    yield
    after = _list_tokens_via_api(base_url)
    for t in after:
        if t["prefix"] not in before and t["revoked_at"] is None:
            _revoke_via_api(base_url, t["prefix"])


def test_tokens_page_renders(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/settings/tokens")
    expect(page.get_by_test_id("issue-form")).to_be_visible()
    expect(page.get_by_test_id("t-name")).to_be_visible()
    expect(page.get_by_test_id("t-days")).to_have_value("30")
    expect(page.get_by_test_id("t-submit")).to_be_visible()


def test_issue_token_shows_plaintext_once(
    page: Page, base_url: str, cleanup_tokens
) -> None:
    page.goto(f"{base_url}/settings/tokens")
    page.get_by_test_id("t-name").fill("e2e-test-token")
    page.get_by_test_id("t-days").fill("7")
    page.get_by_test_id("t-submit").click()

    # plaintext panel appears, plaintext begins with hybe_pat_
    panel = page.get_by_test_id("new-token-panel")
    expect(panel).to_be_visible()
    plaintext_el = page.get_by_test_id("new-token-plaintext")
    plaintext = plaintext_el.text_content() or ""
    assert plaintext.startswith("hybe_pat_"), plaintext
    assert len(plaintext) > 20

    # the new token shows up in the table — find by plaintext prefix match
    plaintext_prefix = plaintext[:16]
    via_api = _list_tokens_via_api(base_url)
    matching = [t for t in via_api if t["prefix"] == plaintext_prefix]
    assert len(matching) == 1
    prefix = matching[0]["prefix"]
    expect(page.get_by_test_id(f"token-row-{prefix}")).to_be_visible()
    expect(page.get_by_test_id(f"status-{prefix}")).to_have_text("active")

    # reload — plaintext panel is gone (one-time only)
    page.goto(f"{base_url}/settings/tokens")
    expect(page.get_by_test_id("new-token-panel")).to_have_count(0)
    expect(page.get_by_test_id(f"token-row-{prefix}")).to_be_visible()


def test_revoke_token(page: Page, base_url: str, cleanup_tokens) -> None:
    # issue via API to keep test focused on revoke flow
    r = httpx.post(
        f"{base_url}/api/tokens",
        json={"name": "to-revoke", "expires_in_days": 7},
        verify=False,
    )
    r.raise_for_status()
    prefix = r.json()["prefix"]

    page.goto(f"{base_url}/settings/tokens")
    expect(page.get_by_test_id(f"status-{prefix}")).to_have_text("active")

    # confirm() dialog → accept
    page.on("dialog", lambda d: d.accept())
    page.get_by_test_id(f"revoke-{prefix}").click()

    # row still there but status revoked, no revoke button
    expect(page.get_by_test_id(f"status-{prefix}")).to_have_text("revoked")
    expect(page.get_by_test_id(f"revoke-{prefix}")).to_have_count(0)


def test_issue_empty_name_shows_error(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/settings/tokens")
    # bypass HTML5 required by clearing then submitting via httpx
    resp = form_post(
        base_url, "/settings/tokens/issue",
        data={"name": "   ", "expires_in_days": "7"},
    )
    assert resp.status_code == 200
    assert "이름을 입력" in resp.text


def test_issue_bad_expiry_shows_error(page: Page, base_url: str) -> None:
    resp = form_post(
        base_url, "/settings/tokens/issue",
        data={"name": "bad-days", "expires_in_days": "9999"},
    )
    assert resp.status_code == 200
    assert "expires_in_days" in resp.text or "만료" in resp.text
