"""Tests for the closed, workspace-owned policy profile registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from localmcptools.persistence import db
from localmcptools.policy.profile import (
    DEFAULT_PROFILE,
    Profile,
    ProfileChangeForbidden,
    current,
    set_current_from_tool,
)
from localmcptools.tools.workspace import workspace_register
from localmcptools.workspaces.registry import WorkspaceNotRegistered, register


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "audit.sqlite"
    db.init_db(path)
    return path


@pytest.fixture
def workspace(database: Path, tmp_path: Path) -> str:
    root = tmp_path / "workspace"
    root.mkdir()
    with db.connection(database) as conn:
        return register(root, conn=conn).id


def test_registry_has_exactly_the_four_documented_profiles() -> None:
    assert set(Profile) == {
        Profile.OBSERVE,
        Profile.WORKSPACE_EXEC,
        Profile.MANAGED_PROCESS,
        Profile.INTERACTIVE_UI,
    }
    assert DEFAULT_PROFILE is Profile.OBSERVE


def test_fresh_workspace_profile_is_observe(database: Path, workspace: str) -> None:
    with db.connection(database) as conn:
        assert current(workspace, conn=conn) is Profile.OBSERVE


def test_unknown_workspace_has_no_profile(database: Path) -> None:
    with db.connection(database) as conn:
        with pytest.raises(WorkspaceNotRegistered):
            current("missing", conn=conn)


def test_invalid_persisted_profile_fails_closed(database: Path, workspace: str) -> None:
    with db.connection(database) as conn:
        conn.execute("UPDATE workspaces SET profile = 'admin' WHERE id = ?", (workspace,))
        with pytest.raises(RuntimeError, match="invalid profile"):
            current(workspace, conn=conn)


def test_tool_cannot_change_profile() -> None:
    with pytest.raises(ProfileChangeForbidden):
        set_current_from_tool("workspace-id", Profile.WORKSPACE_EXEC)


def test_agent_supplied_profile_is_ignored_by_workspace_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registration tool must never treat an agent arg as authority."""
    database = tmp_path / "audit.sqlite"
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(db, "audit_db_path", lambda: database)

    result = workspace_register({"path": str(root), "profile": Profile.WORKSPACE_EXEC.value})

    assert result["profile"] == Profile.OBSERVE.value
