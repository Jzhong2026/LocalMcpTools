"""Tests for the workspace tool bodies.

Each test invokes the tool body directly with a synthetic args dict.
The audit / envelope wrapping is covered by the integration tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from localmcptools.persistence import db
from localmcptools.tools.workspace import (
    workspace_inspect,
    workspace_list,
    workspace_register,
    workspace_search_text,
)


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    db.init_db(tmp_path / "audit.sqlite")
    return tmp_path / "audit.sqlite"


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """A small workspace with a marker file and a few source files."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_x.py").write_text("def test_x(): assert True\n")
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("# TODO: implement\nx = 1\n")
    (root / "build.log").write_text("line 0\nline 1\nline 2\n")
    return root


# --- register -------------------------------------------------------------


def test_register_returns_workspace_dict(fresh_db: Path, project_dir: Path) -> None:
    out = workspace_register({"path": str(project_dir)})
    assert "workspace_id" in out
    assert out["profile"] == "observe"
    assert out["canonical_root"].lower().endswith(str(project_dir).lower())


def test_register_is_idempotent(fresh_db: Path, project_dir: Path) -> None:
    a = workspace_register({"path": str(project_dir)})
    b = workspace_register({"path": str(project_dir)})
    assert a["workspace_id"] == b["workspace_id"]


def test_register_rejects_path_escape(fresh_db: Path, tmp_path: Path) -> None:
    with pytest.raises(Exception):
        # ``fail`` raises ``ToolErrorResponse`` which is a subclass of Exception
        workspace_register({"path": str(tmp_path) + "/../etc"})


def test_register_requires_path(fresh_db: Path) -> None:
    with pytest.raises(Exception):
        workspace_register({})


# --- list -----------------------------------------------------------------


def test_list_returns_registered(fresh_db: Path, project_dir: Path) -> None:
    workspace_register({"path": str(project_dir)})
    res = workspace_list({})
    assert len(res["workspaces"]) >= 1
    assert any(
        w["canonical_root"].lower().endswith(str(project_dir).lower())
        for w in res["workspaces"]
    )


# --- inspect --------------------------------------------------------------


def test_inspect_returns_full_payload(fresh_db: Path, project_dir: Path) -> None:
    ws_id = workspace_register({"path": str(project_dir)})["workspace_id"]
    payload = workspace_inspect({"workspace_id": ws_id})
    assert payload["project_type"] == "python"
    assert payload["git"]["status"] == "not_a_repo"
    assert "test" in payload["presets_available"]
    assert "build" in payload["presets_available"]
    assert "runtimes" in payload
    assert "missing_runtimes" in payload


def test_inspect_unknown_workspace_raises(fresh_db: Path) -> None:
    with pytest.raises(Exception):
        workspace_inspect({"workspace_id": "nope"})


# --- search_text ----------------------------------------------------------


def test_search_finds_match(fresh_db: Path, project_dir: Path) -> None:
    ws_id = workspace_register({"path": str(project_dir)})["workspace_id"]
    res = workspace_search_text(
        {"workspace_id": ws_id, "pattern": "TODO", "max_results": 10}
    )
    assert any("TODO" in m["text"] for m in res["matches"])


def test_search_invalid_regex_raises(fresh_db: Path, project_dir: Path) -> None:
    ws_id = workspace_register({"path": str(project_dir)})["workspace_id"]
    with pytest.raises(Exception):
        workspace_search_text({"workspace_id": ws_id, "pattern": "["})


def test_search_skips_default_excluded_dirs(fresh_db: Path, project_dir: Path) -> None:
    (project_dir / "node_modules").mkdir()
    (project_dir / "node_modules" / "x.js").write_text("TODO leaked\n")
    ws_id = workspace_register({"path": str(project_dir)})["workspace_id"]
    res = workspace_search_text(
        {"workspace_id": ws_id, "pattern": "TODO", "max_results": 50}
    )
    # The match in node_modules must NOT be returned.
    files = {m["file"] for m in res["matches"]}
    assert not any("node_modules" in f for f in files)


def test_search_truncates_at_max_results(fresh_db: Path, project_dir: Path) -> None:
    ws_id = workspace_register({"path": str(project_dir)})["workspace_id"]
    res = workspace_search_text(
        {"workspace_id": ws_id, "pattern": "line", "max_results": 1}
    )
    assert len(res["matches"]) == 1
    assert res["truncated"] is True
    assert res["next_actions"]
