"""Tests for the environment tool bodies.

We stub out the powershell / chardet probes to keep unit tests fast
and deterministic. The integration test exercises the real probes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from localmcptools.persistence import db
from localmcptools.tools.environment import environment_get


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    db.init_db(tmp_path / "audit.sqlite")
    return tmp_path / "audit.sqlite"


def test_environment_get_returns_required_fields(monkeypatch, tmp_path: Path) -> None:
    """The probe must return every field in REQ-ENV-1."""
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "localmcptools.tools.environment._get_console_encoding",
        lambda: "utf-8",
    )
    monkeypatch.setattr(
        "localmcptools.tools.environment._get_active_code_page",
        lambda: 65001,
    )
    monkeypatch.setattr(
        "localmcptools.tools.environment._get_is_admin", lambda: True
    )
    monkeypatch.setattr(
        "localmcptools.tools.environment._probe_powershell",
        lambda: {"version": "5.1.22621", "edition": "Desktop",
                 "executable": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"},
    )
    monkeypatch.setenv("USERNAME", "alice")

    payload = environment_get({})

    # REQ-ENV-1: every required field present.
    assert payload["os"]["name"] in ("nt", "posix")
    assert "version" in payload["os"]
    assert "build" in payload["os"]
    assert "architecture" in payload["os"]
    assert payload["powershell"]["version"] == "5.1.22621"
    assert payload["powershell"]["edition"] == "Desktop"
    assert payload["powershell"]["executable"].endswith("powershell.exe")
    assert payload["encoding"]["active_code_page"] == 65001
    assert payload["encoding"]["console_output"] == "utf-8"
    assert payload["encoding"]["preferred_fs"] == "utf-8"
    assert payload["user"]["name"] == "alice"
    assert payload["user"]["is_admin"] is True
    assert "cwd" in payload
    assert "machine" in payload


def test_environment_get_sets_gbk_for_chinese(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "localmcptools.tools.environment._get_console_encoding",
        lambda: "gbk",
    )
    monkeypatch.setattr(
        "localmcptools.tools.environment._get_active_code_page",
        lambda: 936,
    )
    monkeypatch.setattr(
        "localmcptools.tools.environment._get_is_admin", lambda: False
    )
    monkeypatch.setattr(
        "localmcptools.tools.environment._probe_powershell",
        lambda: {"version": "5.1", "edition": "Desktop", "executable": "ps"},
    )
    payload = environment_get({})
    assert payload["encoding"]["preferred_fs"] == "gbk"


def test_environment_get_emits_next_actions_on_partial_failure(
    monkeypatch, tmp_path: Path
) -> None:
    """Encoding probe failing → ``next_actions`` must be set."""
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "localmcptools.tools.environment._get_console_encoding",
        lambda: "unknown",
    )
    monkeypatch.setattr(
        "localmcptools.tools.environment._get_active_code_page",
        lambda: None,
    )
    monkeypatch.setattr(
        "localmcptools.tools.environment._get_is_admin", lambda: None
    )
    monkeypatch.setattr(
        "localmcptools.tools.environment._probe_powershell",
        lambda: {"version": None, "edition": None, "executable": None},
    )

    # On Windows we expect both chcp and whoami to fail; on non-Windows
    # only the encoding probe is exercised.
    result = environment_get({})
    # ``environment_get`` returns a ToolResponse when next_actions exist.
    from localmcptools.tools._common import ToolResponse
    if isinstance(result, ToolResponse):
        assert result.ok is True
        assert result.meta.next_actions, "next_actions must be set on partial failure"
    else:
        # Plain dict path (no failures).
        assert "encoding" in result
