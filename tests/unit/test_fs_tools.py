"""Tests for fs.* tool bodies."""

from __future__ import annotations

from pathlib import Path

import pytest

from localmcptools.persistence import db
from localmcptools.tools.fs import fs_grep_files, fs_read_range, fs_tail_log_file
from localmcptools.tools.workspace import workspace_register


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    db.init_db(tmp_path / "audit.sqlite")
    return tmp_path / "audit.sqlite"


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    (root / "build.log").write_text("\n".join(f"line {i}" for i in range(20)) + "\n")
    (root / "main.py").write_text("# TODO: real\nprint('hi')\n")
    (root / "weird.bin").write_bytes(b"\x00\x01\x02binary")
    return root


def test_fs_read_range(fresh_db: Path, project_dir: Path) -> None:
    ws_id = workspace_register({"path": str(project_dir)})["workspace_id"]
    res = fs_read_range(
        {
            "workspace_id": ws_id,
            "path": "build.log",
            "start_line": 0,
            "end_line": 3,
        }
    )
    assert res["lines"] == ["line 0", "line 1", "line 2"]
    assert res["total_lines"] == 20


def test_fs_read_range_binary_returns_error(fresh_db: Path, project_dir: Path) -> None:
    ws_id = workspace_register({"path": str(project_dir)})["workspace_id"]
    with pytest.raises(Exception):
        fs_read_range({"workspace_id": ws_id, "path": "weird.bin", "start_line": 0, "end_line": 1})


def test_fs_read_range_rejects_escape(fresh_db: Path, project_dir: Path) -> None:
    ws_id = workspace_register({"path": str(project_dir)})["workspace_id"]
    with pytest.raises(Exception):
        fs_read_range(
            {
                "workspace_id": ws_id,
                "path": str(project_dir) + "/../etc/passwd",
                "start_line": 0,
                "end_line": 1,
            }
        )


def test_fs_tail_log_file(fresh_db: Path, project_dir: Path) -> None:
    ws_id = workspace_register({"path": str(project_dir)})["workspace_id"]
    res = fs_tail_log_file({"workspace_id": ws_id, "path": "build.log", "n": 5})
    assert res["lines"] == ["line 15", "line 16", "line 17", "line 18", "line 19"]
    assert res["truncated"] is False


def test_fs_grep_files_finds_matches(fresh_db: Path, project_dir: Path) -> None:
    ws_id = workspace_register({"path": str(project_dir)})["workspace_id"]
    res = fs_grep_files({"workspace_id": ws_id, "pattern": "TODO", "max_results": 10})
    assert any("TODO" in m["text"] for m in res["matches"])


def test_fs_grep_files_skips_binary(fresh_db: Path, project_dir: Path) -> None:
    ws_id = workspace_register({"path": str(project_dir)})["workspace_id"]
    res = fs_grep_files({"workspace_id": ws_id, "pattern": "binary", "max_results": 10})
    # Nothing should match because the binary file is skipped.
    assert res["matches"] == []


def test_fs_grep_files_include_glob(fresh_db: Path, project_dir: Path) -> None:
    ws_id = workspace_register({"path": str(project_dir)})["workspace_id"]
    res = fs_grep_files(
        {"workspace_id": ws_id, "pattern": "TODO", "include_glob": "*.py", "max_results": 10}
    )
    files = {m["file"] for m in res["matches"]}
    assert all(f.endswith(".py") for f in files)
