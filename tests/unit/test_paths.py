"""Tests for :mod:`localmcptools.config.paths` and :mod:`.settings`.

Covers the spike DoD bullet: *missing config.json returns defaults, never raises*.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from localmcptools.config import defaults, paths, settings


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``LMCP_DATA_DIR`` at a temp directory for the duration of one test."""
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    return tmp_path


def test_data_dir_uses_override_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    p = paths.data_dir()
    assert p == tmp_path
    assert p.exists()  # created on first call


def test_data_dir_creates_missing_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "nested" / "deeper"
    monkeypatch.setenv("LMCP_DATA_DIR", str(target))
    p = paths.data_dir()
    assert p == target
    assert p.is_dir()


def test_data_dir_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    assert paths.data_dir() == paths.data_dir() == tmp_path


def test_audit_db_path_is_under_data_dir(isolated_data_dir: Path) -> None:
    assert paths.audit_db_path() == isolated_data_dir / "audit.sqlite"


def test_settings_missing_file_returns_defaults(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ensure no config.json is present.
    assert not (isolated_data_dir / "config.json").exists()
    loaded = settings.load_settings()
    assert loaded == defaults.get_defaults()
    # Frozen defaults: every section must be present.
    assert set(loaded.keys()) == {"version", "server", "security", "workspaces", "audit"}


def test_settings_merge_preserves_defaults(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = isolated_data_dir / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "server": {"log_level": "DEBUG"},
                # Note: no other sections — defaults must fill in.
            }
        ),
        encoding="utf-8",
    )
    loaded = settings.load_settings()
    assert loaded["server"]["log_level"] == "DEBUG"
    # Untouched sections keep defaults.
    assert loaded["security"]["transport_mode"] == "stdio"
    assert loaded["workspaces"]["default_profile"] == "observe"


def test_settings_malformed_json_falls_back(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (isolated_data_dir / "config.json").write_text("{not valid json", encoding="utf-8")
    loaded = settings.load_settings()
    assert loaded == defaults.get_defaults()
    # Operator-friendly warning lands on stderr.
    err = capsys.readouterr().err
    assert "config.json" in err


def test_settings_extra_keys_are_kept(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (isolated_data_dir / "config.json").write_text(
        json.dumps({"future_section": {"x": 1}}),
        encoding="utf-8",
    )
    loaded = settings.load_settings()
    assert loaded["future_section"] == {"x": 1}  # forward-compatible


def test_settings_scalar_override_replaces_dict(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a user types ``"server": "wrong"`` we should not crash; defaults win for that key."""
    (isolated_data_dir / "config.json").write_text(
        json.dumps({"server": "wrong"}),
        encoding="utf-8",
    )
    loaded = settings.load_settings()
    # We only replace server; the rest of the defaults remain.
    assert loaded["security"]["transport_mode"] == "stdio"


def test_settings_path_override(
    isolated_data_dir: Path, tmp_path: Path
) -> None:
    """``load_settings(path=...)`` reads from an explicit file (used by tests)."""
    cfg = tmp_path / "alt.json"
    cfg.write_text(json.dumps({"audit": {"retention_days": 30}}), encoding="utf-8")
    loaded = settings.load_settings(path=cfg)
    assert loaded["audit"]["retention_days"] == 30
    # Defaults still present elsewhere.
    assert loaded["server"]["host"] == "127.0.0.1"


def test_get_defaults_is_a_copy() -> None:
    """Mutating the returned dict must not leak into future calls."""
    d1 = defaults.get_defaults()
    d1["server"]["host"] = "mutated"
    d2 = defaults.get_defaults()
    assert d2["server"]["host"] == "127.0.0.1"


def test_env_override_wins_over_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Sanity: even if APPDATA points elsewhere, LMCP_DATA_DIR wins."""
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    # APPDATA is normally present on Windows; setting it explicitly to something
    # different exercises the priority order.
    monkeypatch.setenv("APPDATA", "/should/not/be/used")
    p = paths.data_dir()
    assert p == tmp_path
    # Clean up any APPDATA side effect we caused.
    if os.environ.get("LMCP_DATA_DIR") is None:
        monkeypatch.delenv("APPDATA", raising=False)