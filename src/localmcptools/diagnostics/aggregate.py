"""One-call aggregator for ``diagnostics.collect``.

Fans out to runtime detection, git status, problems, ports, and recent
failures, then stitches the results together. Sections that overflow
the per-section 64KB inline cap spill to an artifact handle; the
``depth`` parameter controls whether the agent sees full payloads
inline (``"full"``) or summaries (``"summary"``).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from ..persistence import artifacts, db
from ..process import ports
from ..workspaces.inspect import inspect_workspace
from ..workspaces.registry import WorkspaceNotRegistered, resolve

_log = logging.getLogger(__name__)

_SECTION_INLINE_CAP = 64 * 1024
_RECENT_FAILURES_DEFAULT = 5


def _safe(fn: Any, *args: Any, **kwargs: Any) -> tuple[bool, Any]:
    """Run ``fn`` swallowing exceptions; return ``(ok, value)``."""
    try:
        return True, fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — diagnostics is read-only
        _log.debug("diagnostics sub-call failed: %s", exc)
        return False, str(exc)


def _recent_failures(workspace_id: str | None, *, limit: int) -> list[dict[str, Any]]:
    """Last ``limit`` failed rows for the workspace (or all)."""
    def _query(conn: sqlite3.Connection) -> list[sqlite3.Row]:
        if workspace_id:
            return conn.execute(
                "SELECT id, tool, status, error_code, exit_code, "
                "timestamp, duration_ms FROM calls "
                "WHERE workspace_id = ? AND ok = 0 "
                "ORDER BY timestamp DESC LIMIT ?",
                (workspace_id, limit),
            ).fetchall()
        return conn.execute(
            "SELECT id, tool, status, error_code, exit_code, "
            "timestamp, duration_ms FROM calls "
            "WHERE ok = 0 ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()

    try:
        db.init_db()
        with db.connection() as conn:
            rows = _query(conn)
        return [dict(row) for row in rows]
    except Exception as exc:  # noqa: BLE001
        _log.debug("recent failures query failed: %s", exc)
        return []


def _emit_section(payload: Any, *, depth: str) -> tuple[Any, str | None]:
    """Decide inline vs artifact handle.

    Sections under the cap return ``(value, None)``. Sections over the
    cap are JSON-serialised, persisted as an artifact, and the
    caller gets ``(summary, handle)``.
    """
    encoded = json.dumps(payload, ensure_ascii=False, default=str)
    if len(encoded.encode("utf-8")) <= _SECTION_INLINE_CAP or depth == "summary":
        summary = payload if depth == "summary" else payload
        return summary, None
    handle = artifacts.write(encoded, sensitive=True)
    if depth == "full":
        # ``full`` mode keeps the inline payload, just adds a handle for
        # re-fetch. We always spill here regardless of size so the
        # agent can ask for the raw section again later.
        return payload, handle
    summary = _summarise(payload)
    return summary, handle


def _summarise(payload: Any) -> Any:
    """Project a payload down to a stable summary shape.

    The summary is intentionally lossy — its job is to give the
    agent a one-look overview without blowing its context.
    """
    if isinstance(payload, dict):
        return {
            key: (_summarise(value) if isinstance(value, (dict, list)) else value)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return payload[:5] + (["…(truncated)"] if len(payload) > 5 else [])
    return payload


def collect(args: dict[str, Any]) -> Any:
    """Tool body for ``diagnostics.collect``.

    ``workspace_id`` filters git + recent failures to one workspace;
    ``depth`` is ``"summary"`` (default) or ``"full"``. ``limit``
    controls the recent-failure window (default 5, max 50).
    """
    workspace_id = args.get("workspace_id")
    if workspace_id is not None and not isinstance(workspace_id, str):
        return {
            "error": {
                "code": "invalid_args",
                "message": "workspace_id must be a string",
            }
        }
    depth = args.get("depth") or "summary"
    if depth not in ("summary", "full"):
        return {
            "error": {
                "code": "invalid_args",
                "message": "depth must be 'summary' or 'full'",
            }
        }
    limit = int(args.get("limit") or _RECENT_FAILURES_DEFAULT)
    if not 1 <= limit <= 50:
        limit = _RECENT_FAILURES_DEFAULT

    sections: dict[str, Any] = {}
    notes: list[str] = []
    handles: dict[str, str | None] = {}

    # --- git ----------------------------------------------------------
    git_payload: Any = {"status": "not_a_repo"}
    if workspace_id:
        try:
            workspace = resolve(workspace_id)
            root = Path(workspace.canonical_root)
            git_payload = inspect_workspace(root).get("git", git_payload)
        except WorkspaceNotRegistered:
            git_payload = {"status": "not_a_repo", "note": "workspace not registered"}
        except Exception as exc:  # noqa: BLE001
            _log.debug("git probe failed: %s", exc)
    sections["git"], handles["git"] = _emit_section(git_payload, depth=depth)

    # --- runtime ------------------------------------------------------
    # Lazy import to avoid a cycle: tools.runtime -> diagnostics.aggregate.
    from ..tools import runtime as runtime_tools

    ok, value = _safe(runtime_tools.runtime_detect_runtime, {"workspace_id": workspace_id})
    sections["runtime"], handles["runtime"] = _emit_section(
        value if ok else {"error": str(value)}, depth=depth,
    )

    # --- vscode -------------------------------------------------------
    from ..tools import vscode as vscode_tools
    ok, value = _safe(vscode_tools.vscode_get_problems, {})
    if not ok or (isinstance(value, dict) and value.get("error", {}).get("code") == "vscode_not_running"):
        sections["problems"] = []
        handles["problems"] = None
        notes.append("vscode offline")
    else:
        sections["problems"], handles["problems"] = _emit_section(value, depth=depth)

    # --- ports --------------------------------------------------------
    ok, value = _safe(lambda: {"ports": [item.as_dict() for item in ports.list_listening_ports()]})
    sections["ports"], handles["ports"] = _emit_section(
        value if ok else {"error": str(value)}, depth=depth,
    )

    # --- recent failures ----------------------------------------------
    failures = _recent_failures(workspace_id if isinstance(workspace_id, str) else None, limit=limit)
    sections["recent_failures"], handles["recent_failures"] = _emit_section(failures, depth=depth)

    # --- synthesised next_actions -------------------------------------
    next_actions: list[str] = []
    if any(f.get("error_code") == "denied_by_rule" for f in failures):
        next_actions.append("review safety rules for recent denials")
    if any(f.get("error_code") == "timed_out" for f in failures):
        next_actions.append("raise timeout_ms or use process.start_dev_server")
    if isinstance(sections["runtime"], dict) and sections["runtime"].get("missing"):
        next_actions.append(
            "install missing runtimes: " + ", ".join(sections["runtime"]["missing"])
        )
    if not next_actions:
        next_actions.append("no action required")

    return {
        "sections": sections,
        "handles": handles,
        "notes": notes,
        "next_actions": next_actions,
        "collected_at": int(time.time() * 1000),
    }


__all__ = ["collect"]