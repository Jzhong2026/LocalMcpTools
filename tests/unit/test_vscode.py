"""Tests for vscode.* tool bodies.

VS Code state lives under ``%APPDATA%\\Code\\``; we point ``APPDATA``
at a temp dir for each test and write minimal JSON / SQLite fixtures.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from localmcptools.tools import vscode as vscode_tools


@pytest.fixture
def fake_appdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point APPDATA at a temp directory so we never touch real state."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    # Reset module-level caches.
    vscode_tools._APPDATA = str(tmp_path)
    vscode_tools._CODE_USER = tmp_path / "Code" / "User"
    vscode_tools._CODE_LOGS = tmp_path / "Code" / "logs"
    vscode_tools._CODE_STORAGE = tmp_path / "Code" / "User" / "workspaceStorage"
    return tmp_path


def test_vscode_get_problems_reports_not_running(fake_appdata: Path) -> None:
    out = vscode_tools.vscode_get_problems({})
    assert out["error"]["code"] == "vscode_not_running"
    assert "next_actions" in out["error"]


def test_vscode_get_problems_reads_state_vscdb(fake_appdata: Path) -> None:
    storage = fake_appdata / "Code" / "User" / "workspaceStorage" / "abc"
    storage.mkdir(parents=True)
    db_path = storage / "state.vscdb"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE diagnostics (file TEXT, line INTEGER, column INTEGER, severity TEXT, source TEXT, code TEXT, message TEXT)"
        )
        conn.execute(
            "INSERT INTO diagnostics VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("src/foo.py", 12, 5, "error", "mypy", "123", "incompatible types"),
        )
        conn.commit()
    out = vscode_tools.vscode_get_problems({})
    assert "problems" in out
    assert out["problems"][0]["file"] == "src/foo.py"


def test_vscode_get_installed_extensions_parses(fake_appdata: Path) -> None:
    user = fake_appdata / "Code" / "User"
    user.mkdir(parents=True, exist_ok=True)
    (user / "extensions.json").write_text(
        json.dumps([
            {
                "identifier": {"id": "ms-python.python"},
                "name": "Python",
                "version": "2024.0.0",
                "isActive": True,
            },
            {
                "identifier": {"id": "github.copilot-chat"},
                "name": "Copilot Chat",
                "version": "0.20.0",
                "isActive": False,
            },
        ]),
        encoding="utf-8",
    )
    out = vscode_tools.vscode_get_installed_extensions({})
    assert len(out["extensions"]) == 2
    assert out["extensions"][0]["id"] == "ms-python.python"
    assert out["extensions"][1]["is_active"] is False


def test_vscode_get_logs_returns_lines(fake_appdata: Path) -> None:
    # Need at least one VS Code marker for ``_vscode_running`` to return True.
    user = fake_appdata / "Code" / "User"
    user.mkdir(parents=True, exist_ok=True)
    (user / "extensions.json").write_text("[]", encoding="utf-8")
    logs_dir = fake_appdata / "Code" / "logs" / "20260101"
    logs_dir.mkdir(parents=True)
    log_file = logs_dir / "window.log"
    log_file.write_text("\n".join(f"line {i}" for i in range(50)), encoding="utf-8")
    out = vscode_tools.vscode_get_logs({"channel": "Window", "n": 10})
    assert "lines" in out
    assert len(out["lines"]) == 10
    assert out["channel"] == "Window"


def test_vscode_get_logs_returns_empty_when_channel_missing(fake_appdata: Path) -> None:
    user = fake_appdata / "Code" / "User"
    user.mkdir(parents=True, exist_ok=True)
    (user / "extensions.json").write_text("[]", encoding="utf-8")
    out = vscode_tools.vscode_get_logs({"channel": "Window"})
    assert "lines" in out
    assert out["lines"] == []
    assert out["log_path"] is None


def test_vscode_get_debug_sessions_returns_empty_when_no_table(fake_appdata: Path) -> None:
    storage = fake_appdata / "Code" / "User" / "workspaceStorage" / "abc"
    storage.mkdir(parents=True)
    db_path = storage / "state.vscdb"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE unrelated (id INTEGER)")
    out = vscode_tools.vscode_get_debug_sessions({})
    assert out == {"sessions": []}


def test_vscode_get_problems_severity_filter(fake_appdata: Path) -> None:
    storage = fake_appdata / "Code" / "User" / "workspaceStorage" / "abc"
    storage.mkdir(parents=True)
    db_path = storage / "state.vscdb"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE diagnostics (file TEXT, line INTEGER, column INTEGER, severity TEXT, source TEXT, code TEXT, message TEXT)"
        )
        conn.executemany(
            "INSERT INTO diagnostics VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("a.py", 1, 1, "error", "py", "1", "err"),
                ("a.py", 2, 1, "warning", "py", "2", "warn"),
            ],
        )
        conn.commit()
    only_errors = vscode_tools.vscode_get_problems({"severity": "error"})
    assert all(item["severity"] == "error" for item in only_errors["problems"])
    assert only_errors["total"] == 1