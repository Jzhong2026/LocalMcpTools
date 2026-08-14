"""Tests for the runtime.* tool bodies.

We stub out PATH to a deterministic temp directory and verify the
detection logic behaves predictably even when no real interpreter
exists on the test machine.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import localmcptools.tools.runtime as runtime_tools
from localmcptools.tools.runtime import (
    _walk_path_for,
    runtime_detect_runtime,
    runtime_get_env,
    runtime_list_path,
)


@pytest.fixture(autouse=True)
def _reset_runtime_cache() -> Iterator[None]:
    """Clear the module-level 60s cache between tests."""
    runtime_tools._runtime_cache.clear()
    yield
    runtime_tools._runtime_cache.clear()


@pytest.fixture
def fake_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Replace PATH with two directories, each containing python + node.

    ``fake_path/a/`` holds the defaults; ``fake_path/b/`` holds the
    duplicates. The fake exes are empty files — the version probe
    just times out and we accept ``version=None``.
    """
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    for directory in (a, b):
        (directory / "python.exe").write_bytes(b"")
        (directory / "node.exe").write_bytes(b"")
    monkeypatch.setenv("PATH", f"{a};{b}")
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD;.BAT")
    return tmp_path


def test_runtime_detect_runtime_lists_each_path_entry(fake_path: Path) -> None:
    payload = runtime_detect_runtime({})
    assert "python" in payload
    # Both copies are reported; the first one wins ``is_default``.
    assert len(payload["python"]) >= 2
    defaults = [item for item in payload["python"] if item["is_default"]]
    assert len(defaults) == 1


def test_runtime_detect_runtime_missing(fake_path: Path) -> None:
    payload = runtime_detect_runtime({})
    # Fake ``dotnet``/``npm`` are absent, so they end up in ``missing``.
    assert "dotnet" in payload["missing"]
    assert "npm" in payload["missing"]


def test_runtime_get_env_redacts(monkeypatch: pytest.MonkeyPatch) -> None:
    # A bare glpat- prefix matches the provider-PAT redaction.
    monkeypatch.setenv("LMCP_FAKE_TOKEN", "glpat-abcdefghijklmnopqrstuvwx")
    out = runtime_get_env({"name": "LMCP_FAKE_TOKEN"})
    assert out["name"] == "LMCP_FAKE_TOKEN"
    assert "glpat-" not in out["value"]
    assert out["source"] == "process"


def test_runtime_get_env_redacts_assignment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LMCP_FAKE_TOKEN", "api_key=this_is_a_real_secret_value")
    out = runtime_get_env({"name": "LMCP_FAKE_TOKEN"})
    assert "this_is_a_real_secret_value" not in out["value"]


def test_runtime_get_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LMCP_DEFINITELY_NOT_SET", raising=False)
    out = runtime_get_env({"name": "LMCP_DEFINITELY_NOT_SET"})
    assert out == {"name": "LMCP_DEFINITELY_NOT_SET", "value": None, "source": "missing"}


def test_runtime_get_env_rejects_empty_name() -> None:
    out = runtime_get_env({"name": ""})
    assert out["error"]["code"] == "invalid_args"


def test_runtime_list_path_reports_exists_and_file(fake_path: Path) -> None:
    out = runtime_list_path({})
    dirs = {entry["dir"] for entry in out["entries"]}
    assert str(fake_path / "a") in dirs
    for entry in out["entries"]:
        assert "exists" in entry
        assert "is_file" in entry
        assert "executable" in entry


def test_walk_path_for_returns_empty_when_not_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("PATHEXT", ".EXE")
    assert _walk_path_for("definitely-not-a-real-binary") == []
