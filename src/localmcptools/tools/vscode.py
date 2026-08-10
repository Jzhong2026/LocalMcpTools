"""``vscode.*`` tools — read-only inspection of a running VS Code instance.

Four tools, all read-only and all ``observe``-profile-safe:

- :func:`vscode_get_problems` — read the ``state.vscdb`` SQLite under
  ``%APPDATA%\\Code\\User\\workspaceStorage\\<id>`` for diagnostic
  entries. Falls back to scanning files in ``%APPDATA%\\Code\\logs``
  for typed ``[Error]`` / ``[Warning]`` lines if the SQLite file is
  absent (older versions).

- :func:`vscode_get_installed_extensions` — read ``extensions.json``
  under ``%APPDATA%\\Code\\User`` and return ``{id, name, version,
  is_active}``.

- :func:`vscode_get_logs` — tail the most-recent VS Code log file in
  ``%APPDATA%\\Code\\logs\\<date>\\*`` for the named channel. The
  underlying file is read-only.

- :func:`vscode_get_debug_sessions` — read ``debug.sessions`` from
  VS Code's state store. Returns an empty list when nothing is
  running.

If VS Code is not running (no workspace storage, no log files),
all four tools return ``error.code = "vscode_not_running"`` with a
``next_actions`` hint, per REQ-VSC-1 / REQ-VSC-2.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ._common import ToolResponse

_APPDATA = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
_CODE_USER = Path(_APPDATA) / "Code" / "User"
_CODE_LOGS = Path(_APPDATA) / "Code" / "logs"
_CODE_STORAGE = Path(_APPDATA) / "Code" / "User" / "workspaceStorage"

# Cap on returned problems — agent can page through with `fs.tail_log_file`
# or `output.*` if more is needed.
_PROBLEMS_CAP = 1000
# Cap on tail lines for ``vscode.get_logs`` — matches the design.
_LOG_TAIL = 200


def _vscode_running() -> bool:
    """True if any VS Code state file is reachable.

    We don't need to talk to the running instance — its *presence*
    is enough for the four read-only tools here. The tools that
    need a live process (debug sessions) tolerate empty results.
    """
    if not _CODE_USER.exists():
        return False
    if (_CODE_USER / "extensions.json").exists():
        return True
    if _CODE_STORAGE.exists() and any(_CODE_STORAGE.iterdir()):
        return True
    if _CODE_LOGS.exists() and any(_CODE_LOGS.glob("*/")):
        return True
    return False


def _vscode_not_running() -> dict[str, Any]:
    return {
        "error": {
            "code": "vscode_not_running",
            "message": "no VS Code state files found at %APPDATA%\\Code",
            "next_actions": [
                "open VS Code at least once to populate state",
                "fall back to fs.grep_files against the workspace",
            ],
        }
    }


# --- get_problems ---------------------------------------------------------


_PROBLEM_RE = re.compile(
    r"^(?P<file>[^\s].+?)\((?P<line>\d+),(?P<col>\d+)\):\s+"
    r"(?P<severity>error|warning|info|info|warning|error)\s+"
    r"(?P<source>[A-Za-z0-9_]+)\s+(?P<code>[A-Za-z0-9_]+)\s*:\s*(?P<message>.*)$"
)


def _scrape_problems_from_log(log_path: Path) -> list[dict[str, Any]]:
    """Last-resort: pull typed ``[Error] [Warning]`` lines from a log.

    Real VS Code doesn't keep a global problems file; the closest is
    the typed log channel. We accept anything of the form::

        src/foo.py(12,5): error mypy 123: incompatible types

    Anything else is silently dropped.
    """
    problems: list[dict[str, Any]] = []
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = _PROBLEM_RE.match(line.strip())
                if not m:
                    continue
                problems.append(
                    {
                        "file": m.group("file"),
                        "line": int(m.group("line")),
                        "column": int(m.group("col")),
                        "severity": m.group("severity"),
                        "source": m.group("source"),
                        "code": m.group("code"),
                        "message": m.group("message"),
                    }
                )
                if len(problems) >= _PROBLEMS_CAP:
                    break
    except OSError:
        return []
    return problems


def vscode_get_problems(args: dict[str, Any]) -> Any:
    """Tool body for ``vscode.get_problems``."""
    if not _vscode_running():
        return _vscode_not_running()

    severity_filter = args.get("severity")
    if severity_filter is not None and severity_filter not in {
        "error", "warning", "info",
    }:
        return {
            "error": {
                "code": "invalid_args",
                "message": "severity must be one of error|warning|info",
            }
        }

    # Strategy: prefer reading state.vscdb if available, fall back to
    # scanning the most-recent log file. We don't ship a VS Code
    # extension; we work with whatever VS Code left behind.
    problems: list[dict[str, Any]] = []
    for storage_dir in _CODE_STORAGE.glob("*"):
        db_path = storage_dir / "state.vscdb"
        if not db_path.is_file():
            continue
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0) as conn:
                # Best-effort schema probe. We don't commit to a specific
                # table name; we just look for any "diagnostic" / "problem"
                # shaped rows. VS Code's exact schema varies by version.
                cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = {row[0] for row in cursor.fetchall()}
                for table in tables:
                    if "diagnostic" not in table.lower() and "problem" not in table.lower():
                        continue
                    cursor = conn.execute(f"SELECT * FROM {table} LIMIT ?", (_PROBLEMS_CAP,))
                    cols = [d[0] for d in cursor.description]
                    for row in cursor.fetchall():
                        record = dict(zip(cols, row))
                        severity = str(record.get("severity") or record.get("level") or "").lower()
                        if severity_filter and severity != severity_filter:
                            continue
                        problems.append(
                            {
                                "file": record.get("file") or record.get("uri") or "",
                                "line": int(record.get("line") or record.get("range_start_line") or 0),
                                "column": int(record.get("column") or record.get("range_start_column") or 0),
                                "severity": severity or "info",
                                "source": str(record.get("source") or "vscode"),
                                "code": str(record.get("code") or ""),
                                "message": str(record.get("message") or ""),
                            }
                        )
                    if problems:
                        break
                if problems:
                    break
        except sqlite3.Error:
            # Read-only access on a locked vscdb is fine to skip; we'll
            # fall through to the log scrape below.
            continue

    # Fallback: most recent log file
    if not problems:
        logs_dir = _CODE_LOGS / datetime.now().strftime("%Y%m%d")
        if logs_dir.exists():
            candidates = sorted(
                logs_dir.glob("*.log"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for candidate in candidates:
                problems = _scrape_problems_from_log(candidate)
                if problems:
                    break

    if severity_filter:
        problems = [p for p in problems if p["severity"] == severity_filter]
    total = len(problems)
    return {"problems": problems[:_PROBLEMS_CAP], "total": total}


# --- get_installed_extensions ---------------------------------------------


def vscode_get_installed_extensions(args: dict[str, Any]) -> Any:
    """Tool body for ``vscode.get_installed_extensions``."""
    if not _vscode_running():
        return _vscode_not_running()
    ext_json = _CODE_USER / "extensions.json"
    if not ext_json.is_file():
        return {"extensions": []}
    try:
        data = json.loads(ext_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"extensions": []}
    items = data if isinstance(data, list) else data.get("extensions", [])
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        identifier = item.get("identifier") or {}
        if isinstance(identifier, dict):
            out.append(
                {
                    "id": identifier.get("id") or item.get("id") or "",
                    "name": item.get("name") or "",
                    "version": item.get("version") or "",
                    "is_active": bool(item.get("isActive", True)),
                }
            )
        else:
            out.append(
                {
                    "id": str(identifier),
                    "name": item.get("name") or "",
                    "version": item.get("version") or "",
                    "is_active": bool(item.get("isActive", True)),
                }
            )
    return {"extensions": out}


# --- get_logs -------------------------------------------------------------


def _log_candidates(channel: str) -> list[Path]:
    """Find the most recent log file matching ``channel``."""
    if not _CODE_LOGS.exists():
        return []
    candidates: list[Path] = []
    # VS Code organises logs as ``logs/<date>/<executable>-<timestamp>.log``
    # plus channel-specific files (``Window.log``, ``Extension Host.log``).
    channel_slug = re.sub(r"[^A-Za-z0-9]+", "-", channel).strip("-").lower()
    candidates.extend(_CODE_LOGS.glob(f"**/*{channel_slug}*.log"))
    candidates.extend(_CODE_LOGS.glob(f"**/*{channel}*.log"))
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)


def vscode_get_logs(args: dict[str, Any]) -> Any:
    """Tool body for ``vscode.get_logs``.

    Tails the most recent log file matching the channel. The actual
    file is read-only — we never write to ``%APPDATA%\\Code``.
    """
    if not _vscode_running():
        return _vscode_not_running()
    channel = args.get("channel") or "Window"
    if not isinstance(channel, str):
        return {
            "error": {
                "code": "invalid_args",
                "message": "channel must be a string",
            }
        }
    n = int(args.get("n") or _LOG_TAIL)
    if not 1 <= n <= 5000:
        return {
            "error": {
                "code": "invalid_args",
                "message": "n must be between 1 and 5000",
            }
        }
    candidates = _log_candidates(channel)
    if not candidates:
        return {"lines": [], "log_path": None, "channel": channel}
    log_path = candidates[0]
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {
            "error": {
                "code": "internal_error",
                "message": f"could not read log: {exc}",
            }
        }
    lines = text.splitlines()
    tail = lines[-n:] if len(lines) > n else lines
    return {
        "lines": tail,
        "log_path": str(log_path),
        "channel": channel,
    }


# --- get_debug_sessions ---------------------------------------------------


def vscode_get_debug_sessions(args: dict[str, Any]) -> Any:
    """Tool body for ``vscode.get_debug_sessions``.

    Reads ``debug.sessions`` from the most-recent state.vscdb. If no
    running debug sessions exist the table is empty (or absent) and
    we return ``sessions: []``.
    """
    if not _vscode_running():
        return _vscode_not_running()
    for storage_dir in sorted(_CODE_STORAGE.glob("*"), reverse=True):
        db_path = storage_dir / "state.vscdb"
        if not db_path.is_file():
            continue
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0) as conn:
                cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = {row[0] for row in cursor.fetchall()}
                if "debug.sessions" not in tables and "debug_sessions" not in tables:
                    continue
                table = "debug.sessions" if "debug.sessions" in tables else "debug_sessions"
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()
                cols = [d[0] for d in conn.execute(f"SELECT * FROM {table} LIMIT 1").description]
                sessions = []
                for row in rows:
                    record = dict(zip(cols, row))
                    sessions.append(
                        {
                            "id": str(record.get("id") or record.get("session_id") or ""),
                            "name": str(record.get("name") or ""),
                            "type": str(record.get("type") or record.get("kind") or ""),
                            "configuration": str(record.get("configuration") or ""),
                            "state": str(record.get("state") or "unknown"),
                        }
                    )
                return {"sessions": sessions}
        except sqlite3.Error:
            continue
    return {"sessions": []}


# Silence lint complaints about unused imports — datetime/timedelta kept
# around for callers that want to query older log dirs.
_ = datetime, timedelta


__all__ = [
    "vscode_get_problems",
    "vscode_get_installed_extensions",
    "vscode_get_logs",
    "vscode_get_debug_sessions",
]


_ = ToolResponse