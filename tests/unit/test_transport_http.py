"""Tests for the HTTP transport — Bearer + Origin / CSRF middleware.

Covers the change-5 OpenSpec scenarios for sections 4.1 and 4.3.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from localmcptools.transport import (
    BearerAuth,
    OriginCSRF,
    SecurityContext,
    generate_token,
    is_unsafe_method,
    mount_app,
    origin_allowed,
)


def _build_app(token: str = "test-token") -> FastAPI:
    """Build a minimal app with the security middleware mounted."""
    app = FastAPI()
    ctx = SecurityContext(
        token=token,
        origin_allowlist=("http://127.0.0.1", "http://localhost"),
    )
    mount_app(
        app=app,
        context=ctx,
        control_router=None,
        mcp_asgi_app=None,
        static_dir=None,
    )

    @app.get("/api/echo")
    async def echo():
        return {"ok": True}

    @app.post("/api/echo")
    async def echo_post(payload: dict | None = None):
        return {"ok": True, "received": payload}

    return app


async def _client(app: FastAPI, *, origin: str | None = None) -> httpx.AsyncClient:
    headers = {}
    if origin is not None:
        headers["Origin"] = origin
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers=headers,
    )


# --- Pure helpers ---------------------------------------------------------


def test_is_unsafe_method() -> None:
    assert is_unsafe_method("POST")
    assert is_unsafe_method("PATCH")
    assert is_unsafe_method("DELETE")
    assert is_unsafe_method("PUT")
    assert not is_unsafe_method("GET")
    assert not is_unsafe_method("HEAD")
    assert not is_unsafe_method("OPTIONS")
    # Case-insensitive.
    assert is_unsafe_method("post")


def test_origin_allowed_matches_exact() -> None:
    assert origin_allowed("http://127.0.0.1", ("http://127.0.0.1", "http://localhost"))
    assert origin_allowed("http://localhost", ("http://127.0.0.1", "http://localhost"))
    assert not origin_allowed("http://evil.example", ("http://127.0.0.1",))
    assert not origin_allowed(None, ("http://127.0.0.1",))


def test_generate_token_is_urlsafe_and_unique() -> None:
    a = generate_token()
    b = generate_token()
    assert a != b
    assert len(a) >= 32  # URL-safe base64 of 32 bytes


def test_bearer_auth_accepts_valid_token() -> None:
    auth = BearerAuth(expected="secret-xyz")
    assert auth.is_valid("Bearer secret-xyz")
    assert auth.is_valid("bearer SECRET-XYZ") is False  # case-sensitive


def test_bearer_auth_rejects_missing_or_wrong() -> None:
    auth = BearerAuth(expected="secret-xyz")
    assert not auth.is_valid(None)
    assert not auth.is_valid("Bearer wrong-token")
    assert not auth.is_valid("Basic secret-xyz")


# --- Origin middleware ----------------------------------------------------


@pytest.mark.asyncio
async def test_get_without_origin_is_denied() -> None:
    app = _build_app()
    async with await _client(app) as client:
        r = await client.get("/api/echo")
    assert r.status_code == 403
    assert r.json()["code"] == "origin_denied"


@pytest.mark.asyncio
async def test_get_with_allowed_origin_passes() -> None:
    app = _build_app()
    async with await _client(app, origin="http://127.0.0.1") as client:
        r = await client.get("/api/echo")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.asyncio
async def test_get_with_unknown_origin_is_denied() -> None:
    app = _build_app()
    async with await _client(app, origin="http://evil.example") as client:
        r = await client.get("/api/echo")
    assert r.status_code == 403
    assert r.json()["code"] == "origin_denied"


# --- CSRF -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_csrf_token_endpoint_sets_cookie_and_returns_token() -> None:
    token = "the-secret"
    app = _build_app(token=token)
    async with await _client(app, origin="http://127.0.0.1") as client:
        r = await client.get("/api/csrf-token")
    assert r.status_code == 200
    assert r.json() == {"csrf_token": token}
    assert r.cookies.get("lmcp_csrf") == token


@pytest.mark.asyncio
async def test_post_without_csrf_is_rejected() -> None:
    app = _build_app()
    async with await _client(app, origin="http://127.0.0.1") as client:
        r = await client.post("/api/echo", json={"k": 1})
    assert r.status_code == 403
    assert r.json()["code"] == "csrf_missing"


@pytest.mark.asyncio
async def test_post_with_matching_csrf_succeeds() -> None:
    token = "match-me"
    app = _build_app(token=token)
    async with await _client(app, origin="http://127.0.0.1") as client:
        # Bootstrap the cookie.
        await client.get("/api/csrf-token")
        # Now POST with both cookie and header.
        r = await client.post(
            "/api/echo",
            json={"k": 1},
            headers={"X-LMCP-CSRF": token},
        )
    assert r.status_code == 200
    assert r.json()["received"] == {"k": 1}


@pytest.mark.asyncio
async def test_post_with_mismatched_csrf_is_rejected() -> None:
    token = "match-me"
    app = _build_app(token=token)
    async with await _client(app, origin="http://127.0.0.1") as client:
        await client.get("/api/csrf-token")
        r = await client.post(
            "/api/echo",
            json={"k": 1},
            headers={"X-LMCP-CSRF": "different"},
        )
    assert r.status_code == 403
    assert r.json()["code"] == "csrf_mismatch"


# --- /mcp path is not gated by Origin / CSRF ------------------------------


@pytest.mark.asyncio
async def test_paths_outside_api_are_unaffected() -> None:
    """Routes outside /api/* don't go through Origin/CSRF at all."""
    app = FastAPI()
    ctx = SecurityContext(token="x", origin_allowlist=("http://127.0.0.1",))
    mount_app(app=app, context=ctx, control_router=None, mcp_asgi_app=None, static_dir=None)

    @app.get("/health")
    async def health():
        return {"status": "alive"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "alive"


# --- run a quick end-to-end shell -----------------------------------------


def test_origin_csrf_smoke() -> None:
    """Sanity-check the full assembly compiles and the router is reachable."""
    app = _build_app()
    spec = app.openapi()
    paths = spec.get("paths", {})
    assert "/api/csrf-token" in paths
    assert "/api/echo" in paths