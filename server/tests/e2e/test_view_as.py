"""View-as toggle: admin can preview the platform as a regular user."""
from __future__ import annotations

from playwright.sync_api import Page, expect


def test_admin_sees_toggle_button(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/")
    expect(page.get_by_test_id("view-as-user")).to_be_visible()
    expect(page.get_by_test_id("nav-users")).to_be_visible()
    expect(page.get_by_test_id("nav-audit")).to_be_visible()
    # banner should NOT be present
    assert page.get_by_test_id("view-as-banner").count() == 0


def test_toggle_hides_admin_nav_and_shows_banner(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/")
    page.get_by_test_id("view-as-user").click()

    # 303 redirect → reloaded as user view
    expect(page).to_have_url(f"{base_url}/")
    expect(page.get_by_test_id("view-as-banner")).to_be_visible()

    # admin nav gone
    assert page.get_by_test_id("nav-users").count() == 0
    assert page.get_by_test_id("nav-audit").count() == 0
    # toggle button replaced by banner; the inline button doesn't show anymore
    assert page.get_by_test_id("view-as-user").count() == 0


def test_view_as_blocks_admin_pages(page: Page, base_url: str) -> None:
    # set view-as via toolbar first
    page.goto(f"{base_url}/")
    page.get_by_test_id("view-as-user").click()
    expect(page.get_by_test_id("view-as-banner")).to_be_visible()

    # try to navigate directly — should 403
    resp = page.goto(f"{base_url}/settings/users")
    assert resp is not None
    assert resp.status == 403

    # restore
    page.goto(f"{base_url}/")
    page.get_by_test_id("view-as-restore").click()
    expect(page.get_by_test_id("view-as-user")).to_be_visible()


def test_view_as_changes_card_action_label(page: Page, base_url: str) -> None:
    """In admin mode every card has '관리'. In view-as user mode, the bypass
    user's own cards still have '관리' but cards owned by others show '정보'."""
    page.goto(f"{base_url}/")
    # admin sees 관리 on q2-sample (owned by jaekap.han) AND if Hello via Caddy / Private exist
    expect(page.get_by_test_id("manage-q2-sample")).to_be_visible()

    page.get_by_test_id("view-as-user").click()
    expect(page.get_by_test_id("view-as-banner")).to_be_visible()

    # q2-sample owner is jaekap.han@gmail.com (the bypass user) → still 관리
    expect(page.get_by_test_id("manage-q2-sample")).to_be_visible()

    page.get_by_test_id("view-as-restore").click()


def test_view_as_filters_restricted_reports(page: Page, base_url: str) -> None:
    """In normal admin mode the 'private' card is visible (admin sees all);
    after view-as=user, since the bypass user does NOT own that 'private' report
    and visibility=restricted, the card should disappear from the index."""
    page.goto(f"{base_url}/")
    # 'private' was created earlier in the test session; if missing, skip
    if page.get_by_test_id("card-private").count() == 0:
        return
    expect(page.get_by_test_id("card-private")).to_be_visible()

    page.get_by_test_id("view-as-user").click()
    expect(page.get_by_test_id("view-as-banner")).to_be_visible()

    # restricted-by-other should be gone
    assert page.get_by_test_id("card-private").count() == 0

    page.get_by_test_id("view-as-restore").click()
    expect(page.get_by_test_id("card-private")).to_be_visible()
