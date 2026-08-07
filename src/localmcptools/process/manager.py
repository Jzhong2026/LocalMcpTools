"""SQLite repository for lifecycle-bound background processes."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass

import psutil

from ..persistence import db


class ManagedProcessNotFound(LookupError):
    pass


@dataclass(frozen=True)
class ManagedProcess:
    id: str
    workspace_id: str
    preset: str
    command: str
    cwd: str
    pid: int
    log_handle: str
    port: int | None
    started_at: int
    persistent: bool
    status: str
    exit_code: int | None
    finished_at: int | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def create_row(
    *, workspace_id: str, preset: str, command: str, cwd: str, pid: int,
    log_handle: str, conn: sqlite3.Connection | None = None,
) -> ManagedProcess:
    item = ManagedProcess(
        id=f"mp-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
        preset=preset, command=command, cwd=cwd, pid=pid, log_handle=log_handle,
        port=None, started_at=_now_ms(), persistent=False, status="running",
        exit_code=None, finished_at=None,
    )
    _with_connection(conn, lambda c: c.execute(
        "INSERT INTO background_processes "
        "(id,workspace_id,preset,command,cwd,pid,log_handle,port,started_at,persistent,status,exit_code,finished_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (item.id, item.workspace_id, item.preset, item.command, item.cwd, item.pid,
         item.log_handle, item.port, item.started_at, 0, item.status, None, None),
    ))
    return item


def find_by_id(process_id: str, *, conn: sqlite3.Connection | None = None) -> ManagedProcess:
    row = _with_connection(conn, lambda c: c.execute(
        "SELECT * FROM background_processes WHERE id = ?", (process_id,)
    ).fetchone())
    if row is None:
        raise ManagedProcessNotFound(f"managed process {process_id!r} was not found")
    return _from_row(row)


def find_by_pid(pid: int, *, conn: sqlite3.Connection | None = None) -> ManagedProcess | None:
    row = _with_connection(conn, lambda c: c.execute(
        "SELECT * FROM background_processes WHERE pid = ? ORDER BY started_at DESC LIMIT 1",
        (pid,),
    ).fetchone())
    return _from_row(row) if row is not None else None


def find_owner_by_pid(pid: int) -> ManagedProcess | None:
    """Resolve an exact managed PID or one of its descendant processes."""
    exact = find_by_pid(pid)
    if exact is not None:
        return exact
    for item in all_running():
        try:
            if any(child.pid == pid for child in psutil.Process(item.pid).children(recursive=True)):
                return item
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def list_managed(
    workspace_id: str | None = None, *, conn: sqlite3.Connection | None = None,
) -> list[ManagedProcess]:
    def _query(c: sqlite3.Connection) -> list[sqlite3.Row]:
        if workspace_id is None:
            return c.execute("SELECT * FROM background_processes ORDER BY started_at DESC").fetchall()
        return c.execute(
            "SELECT * FROM background_processes WHERE workspace_id = ? ORDER BY started_at DESC",
            (workspace_id,),
        ).fetchall()
    return [_from_row(row) for row in _with_connection(conn, _query)]


def all_running(*, conn: sqlite3.Connection | None = None) -> list[ManagedProcess]:
    return [item for item in list_managed(conn=conn) if item.status == "running"]


def update(item: ManagedProcess, *, conn: sqlite3.Connection | None = None) -> None:
    """Persist all mutable lifecycle fields for a managed-process row."""
    result = _with_connection(conn, lambda c: c.execute(
        "UPDATE background_processes SET port=?, status=?, exit_code=?, finished_at=? "
        "WHERE id=?",
        (item.port, item.status, item.exit_code, item.finished_at, item.id),
    ))
    if result.rowcount != 1:
        raise ManagedProcessNotFound(f"managed process {item.id!r} was not found")


def mark_exited(
    process_id: str, exit_code: int | None, *, conn: sqlite3.Connection | None = None,
) -> None:
    _with_connection(conn, lambda c: c.execute(
        "UPDATE background_processes SET status='exited', exit_code=?, finished_at=? "
        "WHERE id=? AND status='running'", (exit_code, _now_ms(), process_id)
    ))


def set_port(process_id: str, port: int, *, conn: sqlite3.Connection | None = None) -> None:
    _with_connection(conn, lambda c: c.execute(
        "UPDATE background_processes SET port=? WHERE id=?", (port, process_id)
    ))


def _with_connection(conn: sqlite3.Connection | None, action):  # type: ignore[no-untyped-def]
    if conn is not None:
        return action(conn)
    db.init_db()
    with db.connection() as opened:
        return action(opened)


def _from_row(row: sqlite3.Row) -> ManagedProcess:
    return ManagedProcess(
        id=row["id"], workspace_id=row["workspace_id"], preset=row["preset"],
        command=row["command"], cwd=row["cwd"], pid=row["pid"],
        log_handle=row["log_handle"], port=row["port"], started_at=row["started_at"],
        persistent=bool(row["persistent"]), status=row["status"],
        exit_code=row["exit_code"], finished_at=row["finished_at"],
    )


def _now_ms() -> int:
    return int(time.time() * 1000)


__all__ = ["ManagedProcess", "ManagedProcessNotFound", "all_running", "create_row", "find_by_id", "find_by_pid", "find_owner_by_pid", "list_managed", "mark_exited", "set_port", "update"]
