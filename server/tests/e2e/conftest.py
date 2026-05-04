"""Shared fixtures for e2e Playwright tests.

Assumes uvicorn (:8000) and Caddy (:8443) are already running.
Use: cd server && .venv/bin/pytest tests/e2e/ -v
"""
from __future__ import annotations

import os

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

BASE_URL = os.environ.get("HYBE_E2E_BASE_URL", "https://localhost:8443")


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b: Browser = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture()
def context(browser: Browser) -> BrowserContext:
    ctx = browser.new_context(
        ignore_https_errors=True,
        viewport={"width": 1280, "height": 800},
    )
    yield ctx
    ctx.close()


@pytest.fixture()
def page(context: BrowserContext) -> Page:
    return context.new_page()
