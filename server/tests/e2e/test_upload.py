"""브라우저 업로드 UI — file picker → form submit → 카드 + /r/{slug}/ 동작."""
from __future__ import annotations

import io
import time
import zipfile

import httpx
import pytest
from playwright.sync_api import Page, expect

from tests.e2e._helpers import delete_report, form_post, fresh_slug


def _zip_with(content: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in content.items():
            zf.writestr(name, body)
    return buf.getvalue()


@pytest.fixture()
def slug(base_url: str):
    s = fresh_slug("upload")
    yield s
    try:
        delete_report(base_url, s)
    except Exception:
        pass


def test_upload_button_on_index(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/")
    expect(page.get_by_test_id("nav-upload")).to_be_visible()


def test_upload_form_renders(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/upload")
    expect(page.get_by_test_id("upload-form")).to_be_visible()
    expect(page.get_by_test_id("dropzone")).to_be_visible()
    expect(page.get_by_test_id("vis-restricted")).to_be_checked()


def test_full_upload_flow(page: Page, base_url: str, slug: str) -> None:
    page.goto(f"{base_url}/upload")
    page.get_by_test_id("file-input").set_input_files(
        files=[
            {
                "name": f"{slug}.zip",
                "mimeType": "application/zip",
                "buffer": _zip_with(
                    {"index.html": f"<h1>uploaded via browser: {slug}</h1>"}
                ),
            }
        ]
    )
    page.get_by_test_id("f-slug").fill(slug)
    page.get_by_test_id("f-title").fill(f"Upload e2e {slug}")
    page.get_by_test_id("vis-internal").check()
    page.get_by_test_id("f-tags").fill("e2e, upload")
    page.get_by_test_id("upload-submit").click()

    # 303 → /reports/{slug}
    expect(page).to_have_url(f"{base_url}/reports/{slug}")
    expect(page.get_by_test_id("report-title")).to_contain_text(f"Upload e2e {slug}")

    # appears on index
    page.goto(f"{base_url}/")
    expect(page.get_by_test_id(f"card-{slug}")).to_be_visible()

    # served at /r/{slug}/
    resp = httpx.get(f"{base_url}/r/{slug}/", verify=False)
    assert resp.status_code == 200
    assert slug in resp.text


def test_upload_slug_collision_shows_error(page: Page, base_url: str, slug: str) -> None:
    # first upload — succeeds
    form_post(
        base_url, "/upload",
        data={"slug": slug, "title": "v1", "visibility": "internal"},
        files={"file": (f"{slug}.zip", _zip_with({"index.html": "<p>v1</p>"}), "application/zip")},
    )
    # second upload with same slug via UI → 409 + error banner
    page.goto(f"{base_url}/upload")
    page.get_by_test_id("file-input").set_input_files(
        files=[{
            "name": f"{slug}.zip",
            "mimeType": "application/zip",
            "buffer": _zip_with({"index.html": "<p>v2</p>"}),
        }]
    )
    page.get_by_test_id("f-slug").fill(slug)
    page.get_by_test_id("upload-submit").click()
    expect(page.get_by_test_id("upload-error")).to_be_visible()
    expect(page.get_by_test_id("upload-error")).to_contain_text("이미 존재")


def test_upload_missing_file_shows_error(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/upload")
    page.get_by_test_id("f-slug").fill("never-created")
    # don't choose a file
    # the file input has `required` so HTML5 will block — bypass via direct POST
    resp = form_post(
        base_url, "/upload",
        data={"slug": "never-created", "title": "x", "visibility": "internal"},
    )
    # missing file → re-render with error 400
    assert resp.status_code == 400
    assert "zip 파일을 선택" in resp.text
