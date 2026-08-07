"""Tests for the workspace introspection helpers in :mod:`workspaces.inspect`.

These exercise the pure detection logic without spinning up a server.
"""

from __future__ import annotations

from pathlib import Path

from localmcptools.workspaces.inspect import (
    detect_presets,
    detect_project_type,
    detect_runtimes,
    expected_runtimes_for,
    git_status,
    inspect_workspace,
)


def test_detect_project_type_node(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    assert detect_project_type(tmp_path) == "node"


def test_detect_project_type_python(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert detect_project_type(tmp_path) == "python"


def test_detect_project_type_dotnet(tmp_path: Path) -> None:
    (tmp_path / "Foo.csproj").write_text("<Project/>")
    assert detect_project_type(tmp_path) == "dotnet"


def test_detect_project_type_mixed(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert detect_project_type(tmp_path) == "mixed"


def test_detect_project_type_unknown(tmp_path: Path) -> None:
    assert detect_project_type(tmp_path) == "unknown"


def test_git_status_not_a_repo(tmp_path: Path) -> None:
    res = git_status(tmp_path)
    assert res["status"] == "not_a_repo"
    assert res["head"] is None
    assert res["branch"] is None


def test_detect_presets_python(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    presets = detect_presets("python", tmp_path)
    assert "test" in presets
    assert "build" in presets


def test_detect_presets_node(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    presets = detect_presets("node", tmp_path)
    assert "test" in presets
    assert "build" in presets
    assert "dev_server" in presets


def test_expected_runtimes_python() -> None:
    assert "python" in expected_runtimes_for("python", [])


def test_expected_runtimes_node() -> None:
    assert "node" in expected_runtimes_for("node", [])
    assert "npm" in expected_runtimes_for("node", [])


def test_expected_runtimes_dotnet() -> None:
    assert "dotnet" in expected_runtimes_for("dotnet", [])


def test_inspect_workspace_returns_full_payload(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x(): assert True\n")
    payload = inspect_workspace(tmp_path)
    assert payload["project_type"] == "python"
    assert payload["git"]["status"] == "not_a_repo"
    assert "test" in payload["presets_available"]
    assert "runtimes" in payload
    assert "missing_runtimes" in payload
    # ``python`` should be missing on a CI box without python3 on PATH,
    # but at least the structure is right.
    assert isinstance(payload["missing_runtimes"], list)


def test_detect_runtimes_returns_list(tmp_path: Path) -> None:
    """The shape is the same regardless of what's installed."""
    res = detect_runtimes()
    assert isinstance(res, list)
    for r in res:
        assert set(r.keys()) == {"name", "version", "path"}
