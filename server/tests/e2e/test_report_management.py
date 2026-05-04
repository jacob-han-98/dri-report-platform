"""Slice 6 — report detail management UI: viewers, visibility, delete."""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e._helpers import delete_report, fresh_slug, upload_report


@pytest.fixture()
def report(base_url: str):
    """Create a fresh report for the test, clean up after."""
    slug = fresh_slug("mgmt")
    upload_report(
        base_url,
        slug=slug,
        title=f"Slice 6 test {slug}",
        visibility="internal",
        tags=["e2e"],
    )
    yield {"slug": slug}
    # best-effort cleanup
    try:
        delete_report(base_url, slug)
    except Exception:
        pass


def test_add_and_remove_viewer(page: Page, base_url: str, report) -> None:
    slug = report["slug"]
    page.goto(f"{base_url}/reports/{slug}")
    expect(page.get_by_test_id("viewers-panel")).to_be_visible()
    expect(page.get_by_test_id("no-viewers")).to_be_visible()

    page.get_by_test_id("add-viewer-email").fill("sarah@hybecorp.com")
    page.get_by_test_id("add-viewer-submit").click()

    # 303 → reloaded same page; viewer row should appear
    expect(page).to_have_url(re.compile(rf"/reports/{slug}"))
    expect(page.get_by_test_id("viewer-sarah@hybecorp.com")).to_be_visible()
    assert page.get_by_test_id("no-viewers").count() == 0

    # remove
    page.get_by_test_id("remove-viewer-sarah@hybecorp.com").click()
    expect(page.get_by_test_id("no-viewers")).to_be_visible()
    assert page.get_by_test_id("viewer-sarah@hybecorp.com").count() == 0


def test_visibility_toggle(page: Page, base_url: str, report) -> None:
    slug = report["slug"]
    page.goto(f"{base_url}/reports/{slug}")

    # initial = internal (we created it that way); badge says INTERNAL
    badge_locator = page.locator(".badge.internal").first
    expect(badge_locator).to_be_visible()

    page.get_by_test_id("visibility-select").select_option("restricted")
    page.locator("[data-testid='visibility-form'] button[type=submit]").click()

    # after redirect: badge should be RESTRICTED
    expect(page).to_have_url(re.compile(rf"/reports/{slug}"))
    expect(page.locator(".badge.restricted").first).to_be_visible()


def test_delete_report_redirects_home(page: Page, base_url: str) -> None:
    # don't use fixture (we want delete to actually drop it)
    slug = fresh_slug("del")
    upload_report(base_url, slug=slug, title="to delete", visibility="internal")

    page.goto(f"{base_url}/reports/{slug}")
    # auto-confirm the native dialog
    page.on("dialog", lambda d: d.accept())
    page.get_by_test_id("delete-report").click()

    expect(page).to_have_url(f"{base_url}/")
    # the deleted card should not be on the index
    assert page.get_by_test_id(f"card-{slug}").count() == 0

    # subsequent /reports/{slug} → 404
    resp = page.goto(f"{base_url}/reports/{slug}")
    assert resp is not None
    assert resp.status == 404
