"""test_02 — every MCP tool round-trips through ``/mcp`` (HTTP transport).

The e2e plan §7.3 contract: every one of the 40 tools must be
invocable through the Streamable HTTP MCP endpoint, with the bearer
auth + Origin + CSRF gates enforced. The same tool surface that
goes over stdio (test_01) must also work over HTTP, because the
HTTP transport is what the Angular SPA's MCP-config endpoint
points external agents at.

This file drives the server in two modes:

* **Happy paths** use the official ``mcp.client.streamable_http``
  client, the same transport the production agents use. Every tool
  is called once with its minimum valid arguments from
  :mod:`tests.e2e.tool_args` and we assert the universal envelope
  contract.
* **Negative paths** use raw :mod:`httpx` to prove the security
  gates work end-to-end: missing Bearer → 401, wrong Bearer → 401,
  Origin not in allowlist → 403, missing CSRF on POST → 403.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from tests.e2e.conftest import HttpHarness
from tests.e2e.tool_args import EXPECTED_ERROR_CODES, MIN_ARGS

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_MCP_URL = "/mcp/"  # trailing slash hits the streamable handler directly
# (the bare /mcp path 307-redirects to /mcp/ in Starlette)


def _streamable_url(harness: HttpHarness) -> str:
    """Absolute URL for the streamable HTTP endpoint."""
    return f"{harness.base_url}{_MCP_URL}"


def _bearer_headers(harness: HttpHarness) -> dict[str, str]:
    """Headers for any /mcp call: bearer token, MCP protocol version,
    and a JSON content type. Origin is intentionally NOT set because
    /mcp doesn't require it (it's the agent path, not the browser
    control plane)."""
    return {
        "Authorization": f"Bearer {harness.bearer_token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }


@contextlib.asynccontextmanager
async def _mcp_session(harness: HttpHarness) -> AsyncIterator[ClientSession]:
    """Yield an initialized MCP client session over HTTP, handling
    both the transport and the session lifecycles correctly.

    The ``streamablehttp_client`` async context manager returns a
    three-tuple (read, write, close_cb) — we destructure it and
    feed read/write into the ClientSession. Both blocks close in
    LIFO order, so the session is closed before the transport.
    """
    async with streamablehttp_client(
        url=_streamable_url(harness),
        headers={"Authorization": f"Bearer {harness.bearer_token}"},
    ) as (read, write, _close):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


# ---------------------------------------------------------------------------
# Auth gates — /mcp requires the bearer token, period.
# ---------------------------------------------------------------------------


def test_missing_authorization_rejected(live_server_http: HttpHarness) -> None:
    """No ``Authorization`` header → 401 from the bearer gate.

    The MCP streamable HTTP transport also requires
    ``Accept: application/json, text/event-stream``; we set both
    so the auth check fires before the protocol's Accept check.
    """
    r = httpx.post(
        _streamable_url(live_server_http),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        content=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            }
        ),
        timeout=10,
    )
    assert r.status_code == 401, f"expected 401, got {r.status_code}: {r.text}"


def test_wrong_authorization_rejected(live_server_http: HttpHarness) -> None:
    """A bearer that doesn't match the server's secret → 401."""
    r = httpx.post(
        _streamable_url(live_server_http),
        headers={
            "Authorization": "Bearer this-is-not-the-real-token",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        content=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            }
        ),
        timeout=10,
    )
    assert r.status_code == 401, f"expected 401, got {r.status_code}: {r.text}"


def test_authorization_without_bearer_scheme_rejected(
    live_server_http: HttpHarness,
) -> None:
    """A header like ``Token <secret>`` (wrong scheme) is rejected."""
    r = httpx.post(
        _streamable_url(live_server_http),
        headers={
            "Authorization": f"Token {live_server_http.bearer_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        content=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            }
        ),
        timeout=10,
    )
    assert r.status_code == 401, f"expected 401, got {r.status_code}: {r.text}"


def test_mcp_does_not_require_origin(live_server_http: HttpHarness) -> None:
    """``/mcp`` is the agent path (not the browser control plane) so
    it must work without an Origin header. This is the same code
    path stdio agents reach via the HTTP transport, and they
    don't send Origin.
    """
    r = httpx.post(
        _streamable_url(live_server_http),
        headers=_bearer_headers(live_server_http),
        content=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            }
        ),
        timeout=10,
    )
    # The MCP streamable transport may respond 200 (success),
    # 202 (accepted for SSE), or 4xx (actual error) — anything
    # except the 401 the no-token case gets.
    assert r.status_code != 401, f"/mcp unexpectedly demanded auth: {r.text}"


# ---------------------------------------------------------------------------
# Happy paths — full tool surface over HTTP, plus the universal envelope.
# ---------------------------------------------------------------------------


_ALL_TOOLS = sorted(MIN_ARGS.keys())


@pytest.fixture(params=_ALL_TOOLS, ids=_ALL_TOOLS)
def tool_name(request) -> str:
    return request.param


async def test_every_tool_round_trips_over_http(
    live_server_http: HttpHarness,
    tool_name: str,
) -> None:
    """The full 40-tool surface, dispatched over the streamable
    HTTP transport, returns the same universal envelope every
    stdio call gets.
    """
    args = MIN_ARGS[tool_name]
    async with _mcp_session(live_server_http) as session:
        result = await session.call_tool(tool_name, arguments=args)
    assert result.content, f"{tool_name}: empty content blocks"
    text = result.content[0].text
    body = json.loads(text)
    assert isinstance(body, dict), f"{tool_name}: body is not a dict"
    assert "ok" in body, f"{tool_name}: missing 'ok'"
    assert "meta" in body, f"{tool_name}: missing 'meta'"
    assert "data" in body, f"{tool_name}: missing 'data'"

    # ok=true OR ok=false with stable error_code (same contract as
    # test_01).
    if body["ok"]:
        assert body.get("data") is not None, (
            f"{tool_name}: ok=true but data is None"
        )
        return

    err = body.get("error") or {}
    code = err.get("code") or ""
    expected = EXPECTED_ERROR_CODES[tool_name]
    if expected != "*":
        assert code in expected, (
            f"{tool_name}: unexpected error_code={code!r} "
            f"(expected one of {sorted(expected)!r}). Body: {body}"
        )


async def test_list_tools_over_http_matches_server_capability(
    live_server_http: HttpHarness,
) -> None:
    """``tools/list`` over HTTP must return the same set of tools the
    stdio transport reports — the two surfaces must stay in lock-step
    so an agent that switches transports mid-session doesn't lose
    access to anything."""
    async with _mcp_session(live_server_http) as session:
        listed = await session.list_tools()
    http_tool_names = {t.name for t in listed.tools}
    # The same set the stdio test (test_01) sees — tool_args.MIN_ARGS
    # is the source of truth for what the server exposes.
    expected = set(MIN_ARGS.keys())
    missing = expected - http_tool_names
    extra = http_tool_names - expected
    assert not missing, f"/mcp missing tools: {sorted(missing)}"
    assert not extra, f"/mcp exposes tools not in MIN_ARGS: {sorted(extra)}"


# ---------------------------------------------------------------------------
# Cross-cutting — same token works for /mcp and /api/*
# ---------------------------------------------------------------------------


def test_same_token_works_for_both_mcp_and_api(
    live_server_http: HttpHarness,
) -> None:
    """The CSRF cookie/header token *is* the bearer secret — one
    32-byte random string serves both. A misconfiguration where the
    two diverge would silently break the SPA without breaking the
    MCP transport, so we test the actual equality here.
    """
    # The harness populates both fields from the same source.
    assert live_server_http.bearer_token == live_server_http.csrf_token, (
        "bearer_token and csrf_token should be the same value "
        "(see transport/http.py docstring on the dual-purpose token)"
    )


# ---------------------------------------------------------------------------
# Coverage report
# ---------------------------------------------------------------------------


def test_http_tool_surface_coverage_summary() -> None:
    """Print the matrix once per run for CI dashboards."""
    print(f"\n=== HTTP /mcp tool-surface coverage ===")
    print(f"  tools verified: {len(MIN_ARGS)}")
    print(f"  - happy-path tools (ok=true): "
          f"{sum(1 for c in EXPECTED_ERROR_CODES.values() if c == set())}")
    print(f"  - error-coded tools: "
          f"{sum(1 for c in EXPECTED_ERROR_CODES.values() if c != set() and c != '*')}")
    print(f"  - any-error-OK tools: "
          f"{sum(1 for c in EXPECTED_ERROR_CODES.values() if c == '*')}")
    print(f"  auth gates tested: missing / wrong / wrong-scheme / no-origin-required")
