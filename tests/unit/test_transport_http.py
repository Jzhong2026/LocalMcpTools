"""Tests for the HTTP transport — separate MCP bearer and control-plane session."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from localmcptools.transport import (
    BearerAuth,
    SecurityContext,
    generate_token,
    host_allowed,
    is_unsafe_method,
    mount_app,
    origin_allowed,
)

MCP = "mcp-bearer-test-value"
ADMIN = "admin-secret-test-value"


def _build_app(
    *,
    mcp_bearer: str = MCP,
    admin_secret: str = ADMIN,
    allowed_hosts: tuple[str, ...] = ("testserver",),
    origin_allowlist: tuple[str, ...] = ("http://127.0.0.1", "http://localhost"),
) -> FastAPI:
    app = FastAPI()
    ctx = SecurityContext(
        mcp_bearer=mcp_bearer,
        admin_secret=admin_secret,
        origin_allowlist=origin_allowlist,
        allowed_hosts=allowed_hosts,
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
    async def echo_post(payload: dict[str, object] | None = None) -> dict[str, object]:
        return {"ok": True, "received": payload}

    @app.post("/mcp")
    async def mcp_probe():
        return {"mcp": True}

    return app


def _client(app: FastAPI, *, origin: str | None = None) -> httpx.AsyncClient:
    headers = {}
    if origin is not None:
        headers["Origin"] = origin
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers=headers,
    )


async def _login(client: httpx.AsyncClient, secret: str = ADMIN) -> str:
    response = await client.post("/api/login", json={"token": secret})
    assert response.status_code == 200, response.text
    csrf = response.json()["csrf_token"]
    assert csrf != MCP
    assert csrf != ADMIN
    client.headers["X-LMCP-CSRF"] = csrf
    return csrf


def test_is_unsafe_method() -> None:
    assert is_unsafe_method("POST")
    assert is_unsafe_method("PATCH")
    assert is_unsafe_method("DELETE")
    assert is_unsafe_method("PUT")
    assert not is_unsafe_method("GET")
    assert not is_unsafe_method("HEAD")
    assert not is_unsafe_method("OPTIONS")
    assert is_unsafe_method("post")


def test_origin_allowed_matches_exact_and_loopback_port() -> None:
    assert origin_allowed("http://127.0.0.1", ("http://127.0.0.1", "http://localhost"))
    assert origin_allowed("http://localhost", ("http://127.0.0.1", "http://localhost"))
    assert origin_allowed("http://127.0.0.1:7890", ("http://127.0.0.1",))
    assert not origin_allowed("http://evil.example", ("http://127.0.0.1",))
    assert not origin_allowed(None, ("http://127.0.0.1",))


def test_host_allowed_loopback_and_configured_tunnel() -> None:
    assert host_allowed("127.0.0.1:7890", ())
    assert host_allowed("localhost", ())
    assert host_allowed("[::1]:7890", ())
    assert host_allowed("xyz.trycloudflare.com", ("xyz.trycloudflare.com",))
    assert not host_allowed("evil.example", ("xyz.trycloudflare.com",))
    assert not host_allowed(None, ())


def test_generate_token_is_urlsafe_and_unique() -> None:
    a = generate_token()
    b = generate_token()
    assert a != b
    assert len(a) >= 32


def test_shared_secret_is_rejected() -> None:
    with pytest.raises(ValueError):
        SecurityContext(mcp_bearer="same-secret", admin_secret="same-secret")


def test_bearer_auth_accepts_valid_token() -> None:
    auth = BearerAuth(expected="secret-xyz")
    assert auth.is_valid("Bearer secret-xyz")
    assert auth.is_valid("bearer SECRET-XYZ") is False


def test_bearer_auth_rejects_missing_or_wrong() -> None:
    auth = BearerAuth(expected="secret-xyz")
    assert not auth.is_valid(None)
    assert not auth.is_valid("Bearer wrong-token")
    assert not auth.is_valid("Basic secret-xyz")


@pytest.mark.asyncio
async def test_get_without_session_is_unauthorized() -> None:
    app = _build_app()
    async with _client(app, origin="http://127.0.0.1") as client:
        response = await client.get("/api/echo")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"
    assert MCP not in response.text
    assert ADMIN not in response.text


@pytest.mark.asyncio
async def test_forged_origin_does_not_issue_csrf_or_bearer() -> None:
    app = _build_app()
    async with _client(app, origin="http://evil.example") as client:
        response = await client.get("/api/csrf-token")
    assert response.status_code == 403
    assert response.json()["code"] == "origin_denied"
    assert "csrf_token" not in response.text
    assert MCP not in response.text


@pytest.mark.asyncio
async def test_login_then_get_and_post() -> None:
    app = _build_app()
    async with _client(app, origin="http://127.0.0.1") as client:
        csrf = await _login(client)
        got = await client.get("/api/echo")
        posted = await client.post("/api/echo", json={"k": 1})
        token = await client.get("/api/csrf-token")
    assert got.status_code == 200
    assert posted.status_code == 200
    assert posted.json()["received"] == {"k": 1}
    assert token.json() == {"csrf_token": csrf}
    assert token.json()["csrf_token"] != MCP


@pytest.mark.asyncio
async def test_cookie_header_pair_without_session_is_rejected() -> None:
    app = _build_app()
    async with _client(app, origin="http://127.0.0.1") as client:
        client.cookies.set("lmcp_session", "forged-session")
        response = await client.post(
            "/api/echo",
            json={"k": 1},
            headers={"X-LMCP-CSRF": "forged-session"},
        )
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


@pytest.mark.asyncio
async def test_post_without_csrf_is_rejected() -> None:
    app = _build_app()
    async with _client(app, origin="http://127.0.0.1") as client:
        await _login(client)
        del client.headers["X-LMCP-CSRF"]
        response = await client.post("/api/echo", json={"k": 1})
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_missing"


@pytest.mark.asyncio
async def test_post_with_mismatched_csrf_is_rejected() -> None:
    app = _build_app()
    async with _client(app, origin="http://127.0.0.1") as client:
        await _login(client)
        response = await client.post(
            "/api/echo",
            json={"k": 1},
            headers={"X-LMCP-CSRF": "different"},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_mismatch"


@pytest.mark.asyncio
async def test_mcp_bearer_does_not_open_control_plane() -> None:
    app = _build_app()
    async with _client(app, origin="http://127.0.0.1") as client:
        login = await client.post("/api/login", json={"token": MCP})
        audit = await client.get("/api/echo", headers={"Authorization": f"Bearer {MCP}"})
    assert login.status_code == 401
    assert audit.status_code == 401
    assert MCP not in login.text


@pytest.mark.asyncio
async def test_https_forwarding_marks_the_session_cookie_secure() -> None:
    app = _build_app()
    async with _client(app, origin="http://127.0.0.1") as client:
        secure = await client.post(
            "/api/login",
            json={"token": ADMIN},
            headers={"X-Forwarded-Proto": "https"},
        )
        plain = await client.post("/api/login", json={"token": ADMIN})
    assert "secure" in secure.headers["set-cookie"].lower()
    assert "httponly" in secure.headers["set-cookie"].lower()
    assert "secure" not in plain.headers["set-cookie"].lower()


@pytest.mark.asyncio
async def test_one_time_login_link_is_single_use() -> None:
    app = _build_app()
    nonce = app.state.security.sessions.issue_login_nonce()
    async with _client(app) as client:
        first = await client.get("/api/login", params={"token": nonce})
        second = await client.get("/api/login", params={"token": nonce})
    assert first.status_code == 302
    assert first.headers["location"] == "/ui/"
    assert first.cookies.get("lmcp_session")
    assert "csrf_token" not in first.text
    assert second.status_code == 401


@pytest.mark.asyncio
async def test_get_login_rejects_admin_secret_in_the_query() -> None:
    app = _build_app()
    async with _client(app) as client:
        response = await client.get("/api/login", params={"token": ADMIN})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bad_host_is_denied_even_with_a_session() -> None:
    app = _build_app()
    async with _client(app, origin="http://127.0.0.1") as client:
        await _login(client)
        response = await client.get("/api/echo", headers={"Host": "evil.example"})
    assert response.status_code == 403
    assert response.json()["code"] == "host_denied"


@pytest.mark.asyncio
async def test_configured_tunnel_host_is_allowed() -> None:
    app = _build_app(allowed_hosts=("testserver", "xyz.trycloudflare.com"))
    async with _client(app, origin="http://127.0.0.1") as client:
        await _login(client)
        response = await client.get("/api/echo", headers={"Host": "xyz.trycloudflare.com"})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_mcp_route_accepts_only_the_mcp_bearer() -> None:
    app = _build_app()
    async with _client(app) as client:
        missing = await client.post("/mcp")
        admin = await client.post("/mcp", headers={"Authorization": f"Bearer {ADMIN}"})
        ok = await client.post("/mcp", headers={"Authorization": f"Bearer {MCP}"})
    assert missing.status_code == 401
    assert admin.status_code == 401
    assert ok.status_code == 200
    assert ok.json() == {"mcp": True}


@pytest.mark.asyncio
async def test_paths_outside_api_are_unaffected() -> None:
    app = FastAPI()
    ctx = SecurityContext(
        mcp_bearer=MCP,
        admin_secret=ADMIN,
        origin_allowlist=("http://127.0.0.1",),
        allowed_hosts=("testserver",),
    )
    mount_app(app=app, context=ctx, control_router=None, mcp_asgi_app=None, static_dir=None)

    @app.get("/health")
    async def health():
        return {"status": "alive"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_origin_csrf_smoke() -> None:
    app = _build_app()
    paths = app.openapi().get("paths", {})
    assert "/api/csrf-token" in paths
    assert "/api/login" in paths
    assert "/api/echo" in paths
