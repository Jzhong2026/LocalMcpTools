"""Window enumeration + authorization (REQ-VSC-1 / REQ-UI-1).

Two surfaces:

- :func:`list_windows` — enumerate every visible top-level window on the
  current desktop. Returns ``[{process, pid, title, hwnd}]`` for windows
  that pass the credential denylist in :mod:`.safety.filters`.

- :func:`authorize` — create an authorized-windows row keyed by a UUID
  (``window_id``), with a default 60-minute TTL. The row lives in the
  audit SQLite so subsequent ``ui.*`` / ``ocr.*`` tool calls can
  validate against it.

- :func:`revoke` — invalidate a row by id.

- :func:`is_authorized` — internal helper; called by the ``ui.*`` /
  ``ocr.*`` tools to gate every UIA / screenshot call.

The schema migration to ``authorized_windows`` lives in
:mod:`localmcptools.persistence.db` (schema v4).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any

from ..persistence import db
from ..safety.filters import is_visible_process, title_blocked

_log = logging.getLogger(__name__)

DEFAULT_TTL_MS = 60 * 60 * 1000  # 60 minutes per REQ-UI-1


@dataclass(frozen=True)
class WindowSummary:
    process: str
    pid: int
    title: str
    hwnd: int  # native window handle (HWND on Windows)


@dataclass(frozen=True)
class AuthorizedWindow:
    id: str
    process: str
    pid: int
    title: str
    hwnd: int
    issued_at: int
    expires_at: int
    revoked: bool


def list_windows() -> list[WindowSummary]:
    """Enumerate top-level windows.

    On Windows we walk ``uiautomation``'s window walker; on every other
    platform we return ``[]``. Every window passes through
    :func:`safety.filters.is_visible_process` and
    :func:`safety.filters.title_blocked` so credential UIs never
    leak.

    Returns a list of :class:`WindowSummary` with the native ``hwnd``
    so the click / screenshot tools can target it later.
    """
    if os.name != "nt":
        return []
    try:
        import uiautomation as auto  # type: ignore[import-untyped]
    except ImportError:
        return []

    try:
        summaries: list[WindowSummary] = []
        for control in auto.GetRootControl().GetChildren():
            try:
                if control.ControlType != auto.ControlType.WindowControl:
                    continue
                process_name = ""
                pid = 0
                try:
                    process_id, _name = auto.GetWindowProcess(control.NativeWindowHandle)
                    pid = int(process_id) if process_id else 0
                    process_name = _name or ""
                except Exception:  # noqa: BLE001 — UIA raises per-process
                    pass
                if process_name and not is_visible_process(process_name):
                    continue
                title = control.Name or ""
                if title_blocked(title):
                    continue
                summaries.append(
                    WindowSummary(
                        process=process_name,
                        pid=pid,
                        title=title,
                        hwnd=int(control.NativeWindowHandle),
                    )
                )
            except Exception:  # noqa: BLE001 — never crash on a bad window
                continue
        # Fallback: uiautomation's walker skips minimized windows, so the
        # screenshot / click tools can't target them. Supplement with a raw
        # Win32 EnumWindows pass (which includes iconic windows) and add any
        # hwnd the UIA pass missed.
        try:
            seen = {int(s.hwnd) for s in summaries}
            _win32_enum_windows(summaries, seen)
        except Exception as exc:  # noqa: BLE001
            _log.debug("list_windows win32 fallback failed: %s", exc)
        return summaries
    except Exception as exc:  # noqa: BLE001 — UIA can refuse to initialise
        _log.debug("list_windows failed: %s", exc)
        return []


def _win32_enum_windows(
    summaries: list[WindowSummary], seen: set[int]
) -> None:
    """Supplement ``list_windows`` with minimized top-level windows.

    ``uiautomation``'s walker does not yield iconic (minimized) windows, so
    screenshot / click tools can't address them. A raw ``EnumWindows`` pass
    includes those, so we add any hwnd the UIA pass missed. Same safety
    filters (visible-process + title-blocklist) apply as the UIA path.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd, _lparam):
        try:
            if int(hwnd) in seen:
                return True
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value or ""
            if title_blocked(title):
                return True
            # Resolve process name from pid without importing psutil.
            process_name = ""
            try:
                import psutil  # type: ignore[import-untyped]

                proc = psutil.Process(int(pid.value))
                process_name = proc.name() or ""
            except Exception:  # noqa: BLE001 — psutil optional / access denied
                process_name = ""
            if process_name and not is_visible_process(process_name):
                return True
            summaries.append(
                WindowSummary(
                    process=process_name,
                    pid=int(pid.value),
                    title=title,
                    hwnd=int(hwnd),
                )
            )
            seen.add(int(hwnd))
        except Exception:  # noqa: BLE001 — never crash on a bad window
            pass
        return True

    user32.EnumWindows(_callback, 0)


def authorize(
    *,
    hwnd: int,
    process: str,
    pid: int,
    title: str,
    ttl_ms: int = DEFAULT_TTL_MS,
    conn: sqlite3.Connection | None = None,
) -> AuthorizedWindow:
    """Persist a window authorization row and return the result."""
    window_id = uuid.uuid4().hex
    now = _now_ms()
    expires = now + ttl_ms

    def _insert(c: sqlite3.Connection) -> None:
        c.execute(
            "INSERT INTO authorized_windows "
            "(id, process, pid, title, hwnd, issued_at, expires_at, revoked) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (window_id, process, pid, title, hwnd, now, expires, 0),
        )

    if conn is not None:
        _insert(conn)
    else:
        db.init_db()
        with db.connection() as connection:
            _insert(connection)
    return AuthorizedWindow(
        id=window_id,
        process=process,
        pid=pid,
        title=title,
        hwnd=hwnd,
        issued_at=now,
        expires_at=expires,
        revoked=False,
    )


def revoke(*, window_id: str, conn: sqlite3.Connection | None = None) -> bool:
    """Mark an authorization revoked. Returns True iff the row was found."""
    def _do(c: sqlite3.Connection) -> int:
        result = c.execute(
            "UPDATE authorized_windows SET revoked = 1 WHERE id = ?",
            (window_id,),
        )
        return result.rowcount

    if conn is not None:
        return _do(conn) > 0
    db.init_db()
    with db.connection() as connection:
        return _do(connection) > 0


def lookup(*, window_id: str, conn: sqlite3.Connection | None = None) -> AuthorizedWindow | None:
    """Read a row by id. Returns None when not found / revoked / expired."""
    def _query(c: sqlite3.Connection) -> sqlite3.Row | None:
        return c.execute(
            "SELECT id, process, pid, title, hwnd, issued_at, expires_at, revoked "
            "FROM authorized_windows WHERE id = ?",
            (window_id,),
        ).fetchone()

    if conn is not None:
        row = _query(conn)
    else:
        db.init_db()
        with db.connection() as connection:
            row = _query(connection)
    if row is None:
        return None
    now = _now_ms()
    if bool(row["revoked"]) or int(row["expires_at"]) <= now:
        return None
    return AuthorizedWindow(
        id=row["id"],
        process=row["process"],
        pid=row["pid"],
        title=row["title"],
        hwnd=row["hwnd"],
        issued_at=row["issued_at"],
        expires_at=row["expires_at"],
        revoked=bool(row["revoked"]),
    )


def is_authorized(*, window_id: str) -> bool:
    """Convenience wrapper for the ``ui.*`` tools."""
    return lookup(window_id=window_id) is not None


def _now_ms() -> int:
    return int(time.time() * 1000)


__all__ = [
    "AuthorizedWindow",
    "DEFAULT_TTL_MS",
    "WindowSummary",
    "authorize",
    "is_authorized",
    "list_windows",
    "lookup",
    "revoke",
]