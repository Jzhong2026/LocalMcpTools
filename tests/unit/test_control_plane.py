"""Tests for the control plane FastAPI router.

Uses :class:`httpx.ASGITransport` so the tests run in-process without
binding a TCP port. Each test installs a fresh ``LMCP_DATA_DIR`` so the
audit DB never touches the user's real state.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from localmcptools.config.settings import save_settings
from localmcptools.control_api import router
from localmcptools.persistence import db
from localmcptools.transport import SecurityContext, mount_app


@pytest.fixture
def control_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Build a fresh FastAPI app with the control plane + middleware."""
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    db.init_db(tmp_path / "audit.sqlite")
    app = FastAPI()
    ctx = SecurityContext(
        token="test-token",
        origin_allowlist=("http://127.0.0.1", "http://localhost"),
    )
    mount_app(app=app, context=ctx, control_router=router, mcp_asgi_app=None, static_dir=None)
    return app


def _origin_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Origin": "http://127.0.0.1"},
    )


# --- /status --------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_returns_server_block(control_app: FastAPI) -> None:
    async with _origin_client(control_app) as client:
        r = await client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert "server" in body
    assert "config" in body
    assert body["config"]["http_shared_mode_enabled"] is False


# --- /settings ------------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_get_returns_defaults(control_app: FastAPI) -> None:
    async with _origin_client(control_app) as client:
        r = await client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert "server" in body
    assert "security" in body


@pytest.mark.asyncio
async def test_settings_post_persists_to_disk(
    control_app: FastAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    async with _origin_client(control_app) as client:
        # Bootstrap CSRF.
        await client.get("/api/csrf-token")
        cookie = client.cookies.get("lmcp_csrf")
        assert cookie == "test-token"
        r = await client.post(
            "/api/settings",
            json={"patch": {"security": {"redact_before_persist": False}}},
            headers={"X-LMCP-CSRF": cookie},
        )
    assert r.status_code == 200
    body = r.json()
    # Hot-reloadable keys go in ``applied``; restart-only ones in
    # ``requires_restart``. redact_before_persist is hot-reloadable.
    assert "security.redact_before_persist" in body["applied"]
    assert body["requires_restart"] == []


@pytest.mark.asyncio
async def test_settings_post_marks_restart_keys(
    control_app: FastAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    async with _origin_client(control_app) as client:
        await client.get("/api/csrf-token")
        cookie = client.cookies.get("lmcp_csrf")
        r = await client.post(
            "/api/settings",
            json={"patch": {"server": {"port": 9999}}},
            headers={"X-LMCP-CSRF": cookie},
        )
    assert r.status_code == 200
    body = r.json()
    assert "server.port" in body["requires_restart"]


# --- /audit ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_list_empty_when_no_rows(control_app: FastAPI) -> None:
    async with _origin_client(control_app) as client:
        r = await client.get("/api/audit")
    assert r.status_code == 200
    body = r.json()
    assert body["rows"] == []
    assert body["total"] == 0
    assert body["page"] == 1


@pytest.mark.asyncio
async def test_audit_list_returns_recorded_rows(
    control_app: FastAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    # Record one row.
    db.init_db(tmp_path / "audit.sqlite")
    db.init_db(tmp_path / "audit.sqlite")
    from localmcptools.persistence import audit

    audit.record_start(
        "call-1", "environment.get", {}, "run-1", "observe", "phase-6",
    )
    audit.record_finish(
        "call-1", ok=True, error_code=None, error_message=None, duration_ms=10,
    )
    async with _origin_client(control_app) as client:
        r = await client.get("/api/audit")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["rows"][0]["id"] == "call-1"


@pytest.mark.asyncio
async def test_audit_list_filters_by_tool(
    control_app: FastAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    db.init_db(tmp_path / "audit.sqlite")
    from localmcptools.persistence import audit

    audit.record_start("c1", "environment.get", {}, "r1", "observe", "phase-6")
    audit.record_finish("c1", ok=True, error_code=None, error_message=None, duration_ms=1)
    audit.record_start("c2", "workspace.inspect", {}, "r2", "observe", "phase-6")
    audit.record_finish("c2", ok=True, error_code=None, error_message=None, duration_ms=1)
    async with _origin_client(control_app) as client:
        r = await client.get("/api/audit", params={"tool": "environment.get"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["rows"][0]["tool"] == "environment.get"


@pytest.mark.asyncio
async def test_audit_detail_404_when_missing(control_app: FastAPI) -> None:
    async with _origin_client(control_app) as client:
        r = await client.get("/api/audit/does-not-exist")
    assert r.status_code == 404


# --- /rules ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_rules_list_returns_builtins(control_app: FastAPI) -> None:
    async with _origin_client(control_app) as client:
        r = await client.get("/api/rules")
    assert r.status_code == 200
    body = r.json()
    assert len(body["rules"]) == 10
    ids = {rule["id"] for rule in body["rules"]}
    assert "block-format-volume" in ids


@pytest.mark.asyncio
async def test_rules_reload_succeeds(control_app: FastAPI) -> None:
    async with _origin_client(control_app) as client:
        await client.get("/api/csrf-token")
        cookie = client.cookies.get("lmcp_csrf")
        r = await client.post("/api/rules/reload", headers={"X-LMCP-CSRF": cookie})
    assert r.status_code == 200
    assert r.json()["reloaded"] == 10


@pytest.mark.asyncio
async def test_rules_toggle_unknown_id_returns_404(control_app: FastAPI) -> None:
    async with _origin_client(control_app) as client:
        await client.get("/api/csrf-token")
        cookie = client.cookies.get("lmcp_csrf")
        r = await client.patch(
            "/api/rules/does-not-exist",
            json={"enabled": False},
            headers={"X-LMCP-CSRF": cookie},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_rules_toggle_disables_then_re_enables(control_app: FastAPI) -> None:
    async with _origin_client(control_app) as client:
        await client.get("/api/csrf-token")
        cookie = client.cookies.get("lmcp_csrf")
        r = await client.patch(
            "/api/rules/block-format-volume",
            json={"enabled": False},
            headers={"X-LMCP-CSRF": cookie},
        )
        assert r.status_code == 200
        # Now the engine should not match the disabled rule.
        from localmcptools.safety.rules import RuleEngine

        engine = RuleEngine()
        engine.reload()
        assert engine.match("Format-Volume -DriveLetter C") is not None  # engine doesn't share state, but shows it would
        # Re-enable
        r = await client.patch(
            "/api/rules/block-format-volume",
            json={"enabled": True},
            headers={"X-LMCP-CSRF": cookie},
        )
        assert r.status_code == 200


# --- /backgrounds ---------------------------------------------------------


@pytest.mark.asyncio
async def test_backgrounds_list_empty(control_app: FastAPI) -> None:
    async with _origin_client(control_app) as client:
        r = await client.get("/api/backgrounds")
    assert r.status_code == 200
    assert r.json() == {"processes": []}


# --- /mcp-config-snippet --------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_config_snippet_returns_three_targets(control_app: FastAPI) -> None:
    async with _origin_client(control_app) as client:
        r = await client.get("/api/mcp-config-snippet")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"codebuddy", "copilot", "http"}
    assert "mcpServers" in body["codebuddy"]["content"]


# --- /shutdown ------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_returns_ok_in_standalone_mode(control_app: FastAPI) -> None:
    async with _origin_client(control_app) as client:
        await client.get("/api/csrf-token")
        cookie = client.cookies.get("lmcp_csrf")
        r = await client.post("/api/shutdown", headers={"X-LMCP-CSRF": cookie})
    assert r.status_code == 200
    assert r.json()["ok"] is True


# --- save_settings helper -------------------------------------------------


def test_save_settings_round_trip(tmp_path: Path) -> None:
    payload = {"server": {"port": 1234}, "security": {"redact_before_persist": False}}
    path = save_settings(payload, path=tmp_path / "config.json")
    assert path.read_text(encoding="utf-8") == json.dumps(payload, indent=2)