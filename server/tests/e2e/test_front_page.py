"""Slice 5 Front page — page load, key DOM, key interactions."""
from __future__ import annotations

import re

from playwright.sync_api import Page, expect


def test_index_loads_and_shows_cards(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/")
    expect(page).to_have_title(re.compile(r"홈.*Hybe Reports"))

    # user chip shows current bypass user
    chip = page.get_by_test_id("user-chip")
    expect(chip).to_be_visible()
    expect(chip).to_contain_text("jaekap.han@gmail.com")

    # at least one card from the seeded data (q2-sample / hello)
    cards = page.locator("[data-testid^='card-']").filter(
        has_not=page.locator("[data-testid^='card-link-']")
    )
    assert cards.count() >= 1, "expected at least one card"

    # toolbar visible
    expect(page.get_by_test_id("toolbar")).to_be_visible()
    expect(page.get_by_test_id("search-input")).to_be_visible()


def test_search_filters_cards(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/")
    page.get_by_test_id("search-input").fill("q2")
    page.get_by_test_id("search-input").press("Enter")

    # URL reflects the search
    expect(page).to_have_url(re.compile(r"q=q2"))

    # summary mentions the search term
    expect(page.get_by_test_id("summary")).to_contain_text("q2")

    # the q2-sample card remains; non-matching ones (e.g. private) gone
    expect(page.get_by_test_id("card-q2-sample")).to_be_visible()
    assert page.get_by_test_id("card-private").count() == 0


def test_filter_pills(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/")
    page.get_by_test_id("filter-mine").click()
    expect(page).to_have_url(re.compile(r"filter=mine"))

    page.goto(f"{base_url}/")
    page.get_by_test_id("filter-shared").click()
    expect(page).to_have_url(re.compile(r"filter=shared"))


def test_card_manage_link_to_detail(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/")
    # pick whichever sample card exists; q2-sample is created by sample upload
    if page.get_by_test_id("card-q2-sample").count() == 0:
        return  # data not seeded; skip
    page.get_by_test_id("manage-q2-sample").click()
    expect(page).to_have_url(re.compile(r"/reports/q2-sample"))
    expect(page.get_by_test_id("report-title")).to_be_visible()


def test_report_detail_renders(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/reports/q2-sample")
    expect(page.get_by_test_id("report-title")).to_be_visible()
    expect(page.get_by_test_id("report-url")).to_contain_text("/r/q2-sample/")

    # owner sees viewers panel (bypass = admin so always)
    expect(page.get_by_test_id("viewers-panel")).to_be_visible()

    # the "open report" button has the right href
    open_btn = page.get_by_test_id("open-report")
    expect(open_btn).to_have_attribute("href", "/r/q2-sample/")


def test_tokens_page(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/settings/tokens")
    # either there are tokens (table) or none (empty message)
    table_or_empty = (
        page.get_by_test_id("tokens-table").count()
        + page.get_by_test_id("empty-tokens").count()
    )
    assert table_or_empty == 1


def test_404_for_missing_report(page: Page, base_url: str) -> None:
    resp = page.goto(f"{base_url}/reports/does-not-exist-xyz")
    assert resp is not None
    assert resp.status == 404


def test_screenshot_index(page: Page, base_url: str) -> None:
    """Saves a screenshot for visual sanity check (not an assertion)."""
    page.goto(f"{base_url}/")
    page.screenshot(path="/tmp/hybe-index.png", full_page=True)
    page.goto(f"{base_url}/reports/q2-sample")
    page.screenshot(path="/tmp/hybe-detail.png", full_page=True)
    page.goto(f"{base_url}/settings/tokens")
    page.screenshot(path="/tmp/hybe-tokens.png", full_page=True)
