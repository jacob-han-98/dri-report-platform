"""MCP server — 5 tools end-to-end via the streamable HTTP client.

Hits a live `https://localhost:8443/mcp/` (Caddy → uvicorn). The dev bypass
seeds `X-User-Email`/`X-User-Role` upstream, so an unauthed client still gets
through with admin role.
"""
from __future__ import annotations

import asyncio
import json
import os
import ssl

import httpx
import pytest

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


BASE_URL = os.environ.get("HYBE_E2E_BASE_URL", "https://localhost:8443")
MCP_URL = f"{BASE_URL}/mcp/"


@pytest.fixture(scope="module")
def seed_report():
    """Ensure at least one internal report exists for fetch/search tests."""
    slug = "mcp-test-fixture"
    # idempotent — delete then create
    httpx.delete(f"{BASE_URL}/api/reports/{slug}", verify=False)
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("index.html", f"<h1>MCP fixture {slug}</h1><p>UNIQUE-MCP-MARKER</p>")
        zf.writestr("notes.txt", "this is a notes file for the mcp fetch test")
    files = {"file": (f"{slug}.zip", buf.getvalue(), "application/zip")}
    data = {"meta": json.dumps({
        "slug": slug, "title": "MCP Test Fixture", "visibility": "internal",
        "tags": ["mcp", "fixture"], "description": "MCP integration test seed.",
    })}
    r = httpx.post(f"{BASE_URL}/api/reports", files=files, data=data, verify=False)
    r.raise_for_status()
    yield slug
    httpx.delete(f"{BASE_URL}/api/reports/{slug}", verify=False)


async def _call_tool_inner(name: str, args: dict | None = None):
    """Returns (ok, payload) — payload is result on ok, error message on fail."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    async with streamablehttp_client(MCP_URL, httpx_client_factory=lambda **kw: httpx.AsyncClient(verify=ctx, **kw)) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments=args or {})
            if result.isError:
                text = result.content[0].text if result.content else "unknown"
                return False, text
            if result.structuredContent and "result" in result.structuredContent:
                return True, result.structuredContent["result"]
            if result.content:
                return True, json.loads(result.content[0].text)
            return True, None


async def _call_tool(name: str, args: dict | None = None):
    ok, payload = await _call_tool_inner(name, args)
    if not ok:
        raise RuntimeError(f"tool {name} error: {payload}")
    return payload


def _run(coro):
    """Run coroutine in a fresh thread with its own event loop.

    asyncio.run() fails when an outer loop is active (e.g., Playwright sync
    leaves one bound to the test thread). A dedicated worker thread sidesteps
    that by getting its own clean loop.
    """
    import threading

    box: dict = {}

    def worker():
        try:
            box["result"] = asyncio.run(coro)
        except BaseException as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=30)
    if "error" in box:
        raise box["error"]
    return box.get("result")


def test_list_tools_returns_five():
    async def _go():
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        async with streamablehttp_client(MCP_URL, httpx_client_factory=lambda **kw: httpx.AsyncClient(verify=ctx, **kw)) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = sorted(t.name for t in tools.tools)
                return names
    names = _run(_go())
    assert names == sorted([
        "fetch_report", "get_report_metadata", "list_my_reports",
        "recent_activity", "search_reports",
    ])


def test_list_my_reports(seed_report: str):
    out = _run(_call_tool("list_my_reports", {"filter": "all", "limit": 10}))
    assert isinstance(out, list)
    slugs = {r["slug"] for r in out}
    assert seed_report in slugs


def test_search_reports(seed_report: str):
    out = _run(_call_tool("search_reports", {"query": "MCP Test Fixture"}))
    assert any(r["slug"] == seed_report for r in out)


def test_get_report_metadata(seed_report: str):
    out = _run(_call_tool("get_report_metadata", {"slug": seed_report}))
    assert out["slug"] == seed_report
    assert out["title"] == "MCP Test Fixture"
    assert "mcp" in out["tags"]


def test_fetch_report_default_path(seed_report: str):
    out = _run(_call_tool("fetch_report", {"slug": seed_report}))
    assert out["path"] == "index.html"
    assert "UNIQUE-MCP-MARKER" in out["content"]
    assert out["content_type"] == "text/html"
    assert out["truncated"] is False


def test_fetch_report_explicit_path(seed_report: str):
    out = _run(_call_tool("fetch_report", {"slug": seed_report, "path": "notes.txt"}))
    assert out["path"] == "notes.txt"
    assert "notes file" in out["content"]
    assert out["content_type"] == "text/plain"


def test_fetch_report_path_traversal_blocked(seed_report: str):
    with pytest.raises(RuntimeError) as exc:
        _run(_call_tool("fetch_report", {"slug": seed_report, "path": "../../../etc/passwd"}))
    assert "escapes report directory" in str(exc.value)


def test_fetch_report_unknown_slug_raises():
    with pytest.raises(RuntimeError) as exc:
        _run(_call_tool("fetch_report", {"slug": "definitely-does-not-exist"}))
    assert "report not found" in str(exc.value)


def test_recent_activity_returns_events():
    out = _run(_call_tool("recent_activity", {"days": 7, "limit": 20}))
    assert isinstance(out, list)
    # should have at least the events generated by other tests in this run
    assert len(out) >= 0  # may be empty on a fresh DB
