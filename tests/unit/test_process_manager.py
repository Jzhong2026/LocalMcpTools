from __future__ import annotations

from pathlib import Path

import pytest

from localmcptools.persistence import artifacts, db
from localmcptools.process import manager


@pytest.fixture
def process_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    database = tmp_path / "audit.sqlite"
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(db, "audit_db_path", lambda: database)
    db.init_db(database)
    return database


def test_create_find_list_and_exit(process_db: Path) -> None:
    handle = artifacts.create_stream(call_id="managed-test")
    item = manager.create_row(
        workspace_id="ws",
        preset="node-vite",
        command="npx vite",
        cwd="C:\\repo",
        pid=12345,
        log_handle=handle,
    )
    assert manager.find_by_id(item.id).pid == 12345
    assert manager.find_by_pid(12345) == item
    assert manager.list_managed("ws") == [item]
    manager.mark_exited(item.id, 7)
    exited = manager.find_by_id(item.id)
    assert exited.status == "exited"
    assert exited.exit_code == 7
    assert exited.finished_at is not None


def test_unknown_managed_id(process_db: Path) -> None:
    with pytest.raises(manager.ManagedProcessNotFound):
        manager.find_by_id("mp-missing")
