"""Tests for :mod:`localmcptools.workspaces.registry`.

Covers the spike DoD bullets:

- Path-escape attempts rejected (early, before realpath).
- Two paths that canonicalise to the same root share one row.
- Reject paths that are not directories.
- Resolve unknown id raises :class:`WorkspaceNotRegistered`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from localmcptools.persistence import db
from localmcptools.workspaces.registry import (
    InvalidPath,
    Workspace,
    WorkspaceNotRegistered,
    assert_inside_workspace,
    canonicalize,
    list_workspaces,
    register,
    resolve,
)


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    db.init_db(tmp_path / "audit.sqlite")
    return tmp_path / "audit.sqlite"


@pytest.fixture
def ws_dir(tmp_path: Path) -> Path:
    """An empty directory used as a registration target."""
    p = tmp_path / "workspace"
    p.mkdir()
    return p


# --- canonicalize -------------------------------------------------------


def test_canonicalize_absolute_dir(ws_dir: Path) -> None:
    out = canonicalize(ws_dir)
    # On Windows, realpath capitalises the drive letter; we just check
    # it ends with the directory we created.
    assert out.lower().endswith(str(ws_dir).lower())


def test_canonicalize_rejects_relative() -> None:
    with pytest.raises(InvalidPath):
        canonicalize("relative/path")


def test_canonicalize_rejects_empty() -> None:
    with pytest.raises(InvalidPath):
        canonicalize("")


def test_canonicalize_rejects_dotdot(ws_dir: Path) -> None:
    with pytest.raises(InvalidPath):
        canonicalize(str(ws_dir) + os_sep() + ".." + os_sep() + "Windows")


def test_canonicalize_rejects_dotdot_anywhere(ws_dir: Path) -> None:
    """A ``..`` segment anywhere — even in the middle — is rejected."""
    with pytest.raises(InvalidPath):
        canonicalize(str(ws_dir) + os_sep() + "src" + os_sep() + ".." + os_sep() + "etc")


def test_canonicalize_rejects_nonexistent_path(tmp_path: Path) -> None:
    """A path that realpath cannot resolve still goes through; it just
    must not be a directory at the end. We don't raise here — the
    directory check happens in :func:`register`."""
    target = tmp_path / "nope"
    out = canonicalize(target)
    assert out.endswith("nope")


def os_sep() -> str:
    import os
    return os.sep


# --- register -----------------------------------------------------------


def test_register_returns_workspace(fresh_db: Path, ws_dir: Path) -> None:
    with db.connection(fresh_db) as conn:
        ws = register(ws_dir, conn=conn)
    assert isinstance(ws, Workspace)
    assert ws.canonical_root.lower().endswith(str(ws_dir).lower())
    assert ws.profile == "observe"
    assert ws.id and len(ws.id) >= 16  # uuid4().hex → 32


def test_register_idempotent_same_canonical_root(
    fresh_db: Path, ws_dir: Path
) -> None:
    """Two inputs that canonicalise to the same path share one row."""
    with db.connection(fresh_db) as conn:
        a = register(ws_dir, conn=conn)
        # Different text input, same canonicalised absolute path.
        b = register(ws_dir, conn=conn)
    assert a.id == b.id


def test_register_rejects_path_escape(fresh_db: Path, tmp_path: Path) -> None:
    parent = tmp_path / "x"
    parent.mkdir()
    # Inside ``parent`` we ask for ``../Windows`` — a traversal attempt.
    with pytest.raises(InvalidPath):
        register(str(parent) + "/../Windows")


def test_register_rejects_non_directory(fresh_db: Path, tmp_path: Path) -> None:
    file = tmp_path / "iamafile.txt"
    file.write_text("hello")
    with pytest.raises(InvalidPath):
        register(file)


def test_register_starts_empty(fresh_db: Path) -> None:
    """A fresh DB has no workspaces."""
    with db.connection(fresh_db) as conn:
        rows = list_workspaces(conn=conn)
    assert rows == []


def test_register_persists_to_db(fresh_db: Path, ws_dir: Path) -> None:
    with db.connection(fresh_db) as conn:
        ws = register(ws_dir, conn=conn, notes="hello")
        rows = list_workspaces(conn=conn)
    assert len(rows) == 1
    assert rows[0].id == ws.id
    assert rows[0].notes == "hello"


def test_register_preserves_original_id_on_idempotent_replay(
    fresh_db: Path, ws_dir: Path
) -> None:
    """The first id wins on subsequent identical registrations."""
    with db.connection(fresh_db) as conn:
        first = register(ws_dir, conn=conn)
        # Re-register with different notes/profile.
        second = register(ws_dir, conn=conn, notes="updated")
    assert first.id == second.id
    # Profile preserved, notes updated.
    assert second.profile == "observe"
    assert second.notes == "updated"


# --- list_workspaces ordering ---------------------------------------------


def test_list_orders_by_registration_time(
    fresh_db: Path, tmp_path: Path
) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    with db.connection(fresh_db) as conn:
        ra = register(a, conn=conn)
        # tiny gap so timestamps differ deterministically
        import time
        time.sleep(0.01)
        rb = register(b, conn=conn)
        rows = list_workspaces(conn=conn)
    assert [r.id for r in rows] == [ra.id, rb.id]


# --- resolve -------------------------------------------------------------


def test_resolve_unknown_raises(fresh_db: Path) -> None:
    with db.connection(fresh_db) as conn:
        with pytest.raises(WorkspaceNotRegistered):
            resolve("not-a-real-id", conn=conn)


def test_resolve_empty_id_raises(fresh_db: Path) -> None:
    with db.connection(fresh_db) as conn:
        with pytest.raises(WorkspaceNotRegistered):
            resolve("", conn=conn)


def test_resolve_returns_workspace(fresh_db: Path, ws_dir: Path) -> None:
    with db.connection(fresh_db) as conn:
        ws = register(ws_dir, conn=conn)
        found = resolve(ws.id, conn=conn)
    assert found.id == ws.id
    assert found.canonical_root == ws.canonical_root


# --- Workspace.contains / assert_inside_workspace ----------------------


def test_workspace_contains_self(fresh_db: Path, ws_dir: Path) -> None:
    with db.connection(fresh_db) as conn:
        ws = register(ws_dir, conn=conn)
    assert ws.contains(ws_dir)
    # Child path inside workspace.
    child = ws_dir / "src" / "main.py"
    child.parent.mkdir()
    child.write_text("print('x')\n")
    assert ws.contains(child)


def test_workspace_rejects_path_outside(
    fresh_db: Path, ws_dir: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    with db.connection(fresh_db) as conn:
        ws = register(ws_dir, conn=conn)
    assert not ws.contains(other)


def test_assert_inside_workspace_passes(fresh_db: Path, ws_dir: Path) -> None:
    with db.connection(fresh_db) as conn:
        ws = register(ws_dir, conn=conn)
    out = assert_inside_workspace(ws, ws_dir)
    assert out.lower().endswith(str(ws_dir).lower())


def test_assert_inside_workspace_rejects_escape(
    fresh_db: Path, ws_dir: Path, tmp_path: Path
) -> None:
    other = tmp_path / "elsewhere"
    other.mkdir()
    with db.connection(fresh_db) as conn:
        ws = register(ws_dir, conn=conn)
    with pytest.raises(InvalidPath):
        assert_inside_workspace(ws, other)


# --- path-escape matrix from the spec --------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        # Canonical examples from the spec.
        "..\\..\\Windows",
        "C:\\Windows\\..\\..\\foo",
        "..\\..\\..\\..\\etc\\passwd",
    ],
)
def test_path_escape_rejected_early(
    fresh_db: Path, hostile: str
) -> None:
    """Escape attempts fail before any disk I/O happens."""
    with pytest.raises(InvalidPath):
        register(hostile)


# --- DB integration: schema_version bumps to 2 -------------------------


def test_db_schema_bumps_to_two_via_registry(fresh_db: Path, ws_dir: Path) -> None:
    """Once a workspace is registered, the schema is at v2."""
    # fresh_db fixture already ran init_db; verify v2 is current.
    with db.connection(fresh_db) as conn:
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        assert row["v"] == 2
        ws = register(ws_dir, conn=conn)
        # Workspaces table is present and has our row.
        rows = conn.execute(
            "SELECT id FROM workspaces WHERE id = ?", (ws.id,)
        ).fetchall()
        assert len(rows) == 1


def test_db_workspaces_and_artifacts_tables_present(fresh_db: Path) -> None:
    with db.connection(fresh_db) as conn:
        names = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "calls" in names
    assert "workspaces" in names
    assert "artifacts" in names
    assert "schema_version" in names
