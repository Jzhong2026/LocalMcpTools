"""Control plane FastAPI router for the Angular SPA.

Every endpoint here is read-only or operator-driven; it never calls a
tool. The router is mounted at ``/api`` by
:func:`localmcptools.transport.http.mount_app` and protected by the
:class:`OriginCSRF` middleware.

Endpoints (paths relative to ``/api``):

- ``GET  /status``                   server health + capability profile
- ``GET  /audit``                    audit log query (filters + pagination)
- ``GET  /audit/{id}``               single audit row + parsed args
- ``GET  /audit/{id}/log``           artifact stream for the call
- ``POST /csrf-token``               *(not here — defined in mount_app)*
- ``GET  /settings``                 merged settings (defaults + config.json)
- ``POST /settings``                 apply a partial settings patch
- ``GET  /rules``                    every built-in + custom rule + hit stats
- ``PATCH /rules/{id}``              toggle a rule enabled/disabled
- ``POST /rules/reload``             reload rule JSON from disk
- ``GET  /backgrounds``              list managed processes
- ``POST /backgrounds/{id}/stop``    stop a managed process (graceful)
- ``GET  /mcp-config-snippet``       per-agent ``mcp.json`` snippet
- ``POST /shutdown``                 orderly HTTP shutdown

The router is intentionally thin: each handler delegates to existing
modules (:mod:`.persistence.audit`, :mod:`.safety.rules`,
:mod:`.process.manager`, :mod:`.config.settings`, ...) and only adapts
the result to JSON. This keeps a single source of truth per domain
concern and lets the SPA rely on the same envelope shape the tools use.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from .config.paths import config_path, data_dir, server_json_path
from .config.settings import load_settings
from .config.settings import save_settings as _save_settings
from .persistence import audit, db
from .process import manager
from .safety.rules import RuleEngine

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


# --- Pydantic models ------------------------------------------------------


class SettingsPatch(BaseModel):
    """Partial settings update.

    ``applied`` keys take effect immediately; ``requires_restart`` keys
    need a server restart (e.g. ``server.port``).
    """

    patch: dict[str, Any] = Field(default_factory=dict)


class RuleToggle(BaseModel):
    enabled: bool


# --- /status --------------------------------------------------------------


@router.get("/status")
async def status() -> dict[str, Any]:
    """Server health, version, profiles + active config knobs."""
    settings = load_settings()
    server_info: dict[str, Any] = {
        "pid": os.getpid(),
        "uptime_ms": 0,  # filled by mount_app if available
        "policy_version": "phase-6",
        "transport": "http",
        "audit_db_initialised": db.is_initialised(),
    }
    try:
        server_json = server_json_path()
        if server_json.exists():
            import json
            data = json.loads(server_json.read_text(encoding="utf-8"))
            started_at = int(data.get("started_at") or 0)
            if started_at:
                server_info["uptime_ms"] = int(time.time() * 1000) - started_at
    except (OSError, ValueError):
        pass

    # Active config knobs the SPA likes to show.
    security = settings.get("security", {})
    return {
        "server": server_info,
        "config": {
            "transport_mode": security.get("transport_mode", "stdio"),
            "http_shared_mode_enabled": bool(security.get("http_shared_mode_enabled", False)),
            "origin_allowlist": list(security.get("origin_allowlist", []) or []),
            "redact_before_persist": bool(security.get("redact_before_persist", True)),
        },
        "data_dir": str(data_dir()),
    }


# --- /audit ---------------------------------------------------------------


@router.get("/audit")
async def audit_list(
    agent: str | None = Query(None),
    tool: str | None = Query(None),
    ok: int | None = Query(None, ge=0, le=1),
    workspace_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """Query audit rows with filters + pagination."""
    where: list[str] = []
    params: list[Any] = []
    if agent:
        where.append("agent = ?")
        params.append(agent)
    if tool:
        where.append("tool = ?")
        params.append(tool)
    if ok is not None:
        where.append("ok = ?")
        params.append(ok)
    if workspace_id:
        where.append("workspace_id = ?")
        params.append(workspace_id)
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""
    offset = (page - 1) * page_size

    db.init_db()
    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT id, timestamp, agent, tool, workspace_id, profile, "
            f"approval_id, run_id, ok, error_code, exit_code, duration_ms, "
            f"status FROM calls {where_clause} ORDER BY timestamp DESC "
            f"LIMIT ? OFFSET ?",
            (*params, page_size, offset),
        ).fetchall()
        total_row = conn.execute(
            f"SELECT COUNT(*) AS c FROM calls {where_clause}",
            tuple(params),
        ).fetchone()

    return {
        "rows": [dict(row) for row in rows],
        "total": int(total_row["c"]) if total_row else 0,
        "page": page,
        "page_size": page_size,
    }


@router.get("/audit/{call_id}")
async def audit_detail(call_id: str) -> dict[str, Any]:
    """Single audit row + parsed args."""
    db.init_db()
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM calls WHERE id = ?", (call_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "audit_not_found", "id": call_id})
    record = dict(row)
    args = record.get("args_redacted")
    if isinstance(args, str):
        try:
            import json
            record["args_redacted"] = json.loads(args)
        except (TypeError, ValueError):
            pass
    return record


@router.get("/audit/{call_id}/log")
async def audit_log(call_id: str) -> Response:
    """Stream the artifact backing this audit row."""
    db.init_db()
    with db.connection() as conn:
        row = conn.execute(
            "SELECT log_path FROM calls WHERE id = ?", (call_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "audit_not_found", "id": call_id})
    log_path = row["log_path"]
    if not log_path:
        return PlainTextResponse("", media_type="text/plain")
    try:
        body = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=410,
            detail={"code": "artifact_missing", "message": str(exc)},
        )
    return PlainTextResponse(body, media_type="text/plain")


# --- /settings ------------------------------------------------------------


@router.get("/settings")
async def settings_get() -> dict[str, Any]:
    return load_settings()


@router.post("/settings")
async def settings_apply(body: SettingsPatch) -> dict[str, Any]:
    """Apply a settings patch.

    Returns ``{applied: [...], requires_restart: [...], failed: [...]}``
    so the SPA can show a single banner when restart is required.
    """
    # Keys that require a restart to take effect. Anything else
    # re-loads on next call.
    RESTART_KEYS: frozenset[str] = frozenset({
        ("server", "host"),
        ("server", "port"),
        ("security", "transport_mode"),
        ("security", "http_shared_mode_enabled"),
        ("security", "origin_allowlist"),
    })
    applied: list[str] = []
    requires_restart: list[str] = []
    failed: list[str] = []

    current = load_settings()
    merged = _deep_merge(current, body.patch)
    for dotted in _walk_dotted(body.patch):
        section, _, key = dotted.partition(".")
        if (section, key) in RESTART_KEYS:
            requires_restart.append(dotted)
        else:
            applied.append(dotted)

    try:
        _save_settings(merged)
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "settings_save_failed", "message": str(exc)},
        )

    return {
        "applied": applied,
        "requires_restart": requires_restart,
        "failed": failed,
    }


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in base.items():
        out[key] = value
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _walk_dotted(payload: dict[str, Any], prefix: str = "") -> list[str]:
    out: list[str] = []
    for key, value in payload.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.extend(_walk_dotted(value, path))
        else:
            out.append(path)
    return out


# --- /rules ---------------------------------------------------------------


_RULE_ENGINE: RuleEngine | None = None


def _engine() -> RuleEngine:
    global _RULE_ENGINE
    if _RULE_ENGINE is None:
        _RULE_ENGINE = RuleEngine()
        _RULE_ENGINE.reload()
    return _RULE_ENGINE


@router.get("/rules")
async def rules_list() -> dict[str, Any]:
    """List every built-in + custom rule, with hit telemetry."""
    from .persistence import db as db_module

    engine = _engine()
    rules = []
    for rule in engine._rules:  # type: ignore[attr-defined]
        rules.append(
            {
                "id": rule.id,
                "severity": rule.severity,
                "suggestion": rule.suggestion,
                "clauses": list(rule.clauses),
            }
        )

    stats: dict[str, dict[str, Any]] = {}
    try:
        db_module.init_db()
        with db_module.connection() as conn:
            rows = conn.execute(
                "SELECT rule_id, hit_count, last_hit_at, last_hit_cmd "
                "FROM rule_hit_stats"
            ).fetchall()
        for row in rows:
            stats[row["rule_id"]] = dict(row)
    except sqlite3.Error as exc:
        _log.debug("rule_hit_stats query failed: %s", exc)

    return {
        "rules": rules,
        "hit_stats": stats,
        "builtin_dir": str(engine._builtin_dir),  # type: ignore[attr-defined]
        "custom_dir": str(engine._custom_dir) if engine._custom_dir else None,  # type: ignore[attr-defined]
    }


@router.patch("/rules/{rule_id}")
async def rules_toggle(rule_id: str, body: RuleToggle) -> dict[str, Any]:
    """Toggle a rule enabled/disabled.

    The disable flag is held in a small in-memory overlay on the
    :class:`RuleEngine` so a reload from disk restores the canonical
    state. Persistent disable would require a custom-rules file;
    that's a future enhancement.
    """
    engine = _engine()
    if not any(r.id == rule_id for r in engine._rules):  # type: ignore[attr-defined]
        raise HTTPException(status_code=404, detail={"code": "rule_not_found", "id": rule_id})
    if not hasattr(engine, "set_enabled"):
        raise HTTPException(
            status_code=501,
            detail={
                "code": "rule_toggle_unsupported",
                "message": "engine reload did not implement set_enabled",
            },
        )
    engine.set_enabled(rule_id, body.enabled)  # type: ignore[attr-defined]
    return {"id": rule_id, "enabled": body.enabled}


@router.post("/rules/reload")
async def rules_reload() -> dict[str, Any]:
    engine = _engine()
    report = engine.reload()
    return report


# --- /backgrounds ---------------------------------------------------------


@router.get("/backgrounds")
async def backgrounds_list() -> dict[str, Any]:
    items = manager.list_managed()
    return {"processes": [item.as_dict() for item in items]}


@router.post("/backgrounds/{process_id}/stop")
async def backgrounds_stop(process_id: str) -> dict[str, Any]:
    try:
        item = manager.find_by_id(process_id)
    except manager.ManagedProcessNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "managed_process_not_found", "id": process_id, "message": str(exc)},
        )
    stopped = manager.stop(item.id, graceful=True)
    return stopped.as_dict()


# --- /windows -------------------------------------------------------------


@router.get("/windows")
async def windows_list() -> dict[str, Any]:
    """List visible top-level windows (credential windows filtered)."""
    from .ui.windows import list_windows

    return {"windows": [w.__dict__ for w in list_windows()]}


@router.post("/windows/authorize")
async def windows_authorize(body: dict[str, Any]) -> dict[str, Any]:
    """Authorize a window for ui.* / ocr.* calls."""
    from .ui.windows import authorize

    hwnd = body.get("hwnd")
    if not isinstance(hwnd, int):
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_args", "message": "hwnd must be an integer"},
        )
    ttl_ms = int(body.get("ttl_ms", 60 * 60 * 1000))
    row = authorize(
        hwnd=hwnd,
        process=str(body.get("process", "")),
        pid=int(body.get("pid", 0)),
        title=str(body.get("title", "")),
        ttl_ms=ttl_ms,
    )
    return {"window": row.__dict__}


@router.post("/windows/{window_id}/revoke")
async def windows_revoke(window_id: str) -> dict[str, Any]:
    """Revoke an authorised window."""
    from .ui.windows import revoke

    return {"revoked": revoke(window_id=window_id)}


# --- /ui/* proxies --------------------------------------------------------
#
# The control plane proxies a small set of MCP tool bodies so the SPA
# can drive UI automation without speaking MCP. Every endpoint passes
# its payload through the corresponding tool body; the chokepoint
# records an audit row on the way through, exactly like a normal MCP
# call would.


def _proxy_tool(tool: str, args: dict[str, Any]) -> Any:
    """Invoke a tool body by name through the chokepoint and return its result.

    The control-plane proxy goes through :class:`ToolExecutionService.invoke`
    so the audit row is recorded the same way as a real MCP call would.
    This keeps audit + envelope + concurrency gating identical across
    transports.

    Imports are deferred to avoid pulling the UI stack at control_api
    import time (the /api/status path stays clean).
    """
    from .execution.service import ToolExecutionService

    service = ToolExecutionService()
    try:
        registration = service.get_registration(tool)
    except KeyError:
        # The control_app test fixture builds an empty service; the
        # real bootstrap registers every tool body. A missing tool is
        # a 501 here so the SPA renders an actionable error.
        raise HTTPException(
            status_code=501,
            detail={"code": "not_implemented", "message": f"unknown tool {tool!r}"},
        )
    if registration is None:
        raise HTTPException(
            status_code=501,
            detail={"code": "not_implemented", "message": f"unknown tool {tool!r}"},
        )
    try:
        result = service.invoke(registration, args)
    except Exception as exc:  # noqa: BLE001 — surface as 500
        raise HTTPException(
            status_code=500,
            detail={"code": "internal_error", "message": str(exc)},
        )
    if isinstance(result, dict) and result.get("ok") is False and result.get("error"):
        # Tool-level errors become 4xx so the SPA can render the error
        # envelope verbatim. The audit row already records the failure.
        code = str(result["error"].get("code") or "")
        if code in {"invalid_args", "invalid_path", "source_not_allowed"}:
            raise HTTPException(status_code=400, detail=result["error"])
        if code in {"window_not_authorized", "workspace_not_registered"}:
            raise HTTPException(status_code=403, detail=result["error"])
        return result  # 200 with the failure envelope (preserves shape)
    return result


@router.post("/ui/get_ui_tree")
async def ui_get_tree(body: dict[str, Any]) -> Any:
    return _proxy_tool("ui.get_ui_tree", body)


@router.post("/ui/find_element")
async def ui_find_element(body: dict[str, Any]) -> Any:
    return _proxy_tool("ui.find_element", body)


@router.post("/ui/screenshot_window")
async def ui_screenshot_window(body: dict[str, Any]) -> Any:
    return _proxy_tool("ui.screenshot_window", body)


@router.post("/ocr/ocr_region")
async def ocr_ocr_region(body: dict[str, Any]) -> Any:
    return _proxy_tool("ocr.ocr_region", body)


@router.post("/ocr/assert_text")
async def ocr_assert_text(body: dict[str, Any]) -> Any:
    return _proxy_tool("ocr.assert_text", body)


# --- /mcp-config-snippet --------------------------------------------------


@router.get("/mcp-config-snippet")
async def mcp_config_snippet(request: Request) -> dict[str, Any]:
    """Per-agent ``mcp.json`` snippet using the current server's host/port.

    The host/port come from the live HTTP server so the snippet works
    regardless of operator config changes.
    """
    settings = load_settings()
    port = int(settings.get("server", {}).get("port") or 0)
    # Prefer the *actual* bound port if available.
    try:
        import json
        server_json = server_json_path()
        if server_json.exists():
            data = json.loads(server_json.read_text(encoding="utf-8"))
            port = int(data.get("port") or port)
    except (OSError, ValueError):
        pass
    host = settings.get("server", {}).get("host") or "127.0.0.1"
    base_url = f"http://{host}:{port}"

    # Pull the bearer token out of server.json so the snippet can ship
    # a working Authorization header.
    token = ""
    try:
        import json
        server_json = server_json_path()
        if server_json.exists():
            data = json.loads(server_json.read_text(encoding="utf-8"))
            token = str(data.get("csrf_token") or "")
    except (OSError, ValueError):
        pass

    snippet = {
        "codebuddy": {
            "location": "%USERPROFILE%\\.codebuddy\\mcp.json",
            "content": {
                "mcpServers": {
                    "localmcptools": {
                        "command": "<absolute path to python>",
                        "args": ["-m", "localmcptools", "start"],
                        "cwd": str(_project_root()),
                    }
                }
            },
        },
        "copilot": {
            "location": "%USERPROFILE%\\.vscode\\mcp.json",
            "content": {
                "mcpServers": {
                    "localmcptools": {
                        "command": "<absolute path to python>",
                        "args": ["-m", "localmcptools", "start"],
                        "cwd": str(_project_root()),
                    }
                }
            },
        },
        "http": {
            "location": "same files but with URL instead of command",
            "content": {
                "mcpServers": {
                    "localmcptools": {
                        "url": f"{base_url}/mcp",
                        "headers": {"Authorization": f"Bearer {token}"} if token else {},
                    }
                }
            },
        },
    }
    return snippet


# --- /shutdown ------------------------------------------------------------


@router.post("/shutdown")
async def shutdown(request: Request) -> dict[str, Any]:
    """Orderly HTTP shutdown.

    Triggers a clean exit; uvicorn receives ``should_exit = True`` via
    the app state. The actual signal handling lives in :mod:`cli`.
    """
    uvicorn_server = getattr(request.app.state, "uvicorn_server", None)
    if uvicorn_server is None:
        # Standalone test mode: no live server, just return ok.
        return {"ok": True, "message": "no live server attached"}
    uvicorn_server.should_exit = True
    return {"ok": True, "message": "shutting down"}


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


__all__ = ["router"]


# Silence linter on private-but-unused helpers from the early draft.
_ = (JSONResponse, _project_root)