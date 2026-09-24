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


def _process_identity(
    hwnd: int, *, cache: dict[int, tuple[str, int]] | None = None
) -> tuple[str, int]:
    """Resolve ``(process_name, pid)`` for a native window handle.

    ``uiautomation`` does not expose a process-lookup helper in every
    released version. The UIA branch of :func:`list_windows` used to call
    one behind a bare ``except``, so the ``AttributeError`` was swallowed
    and *every* visible window reported ``process=''`` / ``pid=0`` — the
    metadata ``ui.authorize_window`` backfills from had nothing in it.
    Go straight to Win32 for the pid and psutil (a hard runtime
    dependency) for the name.

    Prototypes are declared explicitly: ctypes' default ``c_int``
    argument conversion truncates HWNDs wider than 32 bits, which would
    quietly resolve the wrong process instead of failing. ``cache`` is
    shared across one enumeration pass so the twenty windows a browser
    owns cost one process lookup, not twenty.
    """
    key = int(hwnd)
    if cache is not None:
        hit = cache.get(key)
        if hit is not None:
            return hit

    import ctypes
    from ctypes import wintypes

    result: tuple[str, int] = ("", 0)
    try:
        get_thread_pid = ctypes.WinDLL(
            "user32", use_last_error=True
        ).GetWindowThreadProcessId
        get_thread_pid.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        get_thread_pid.restype = wintypes.DWORD

        pid = wintypes.DWORD()
        get_thread_pid(wintypes.HWND(key), ctypes.byref(pid))
        value = int(pid.value)
    except (OSError, ValueError, AttributeError) as exc:  # noqa: BLE001
        _log.debug("GetWindowThreadProcessId failed for hwnd=%s: %s", hwnd, exc)
        value = 0

    if value:
        try:
            import psutil  # type: ignore[import-untyped]

            result = (psutil.Process(value).name() or "", value)
        except Exception as exc:  # noqa: BLE001 — process may have exited
            _log.debug("could not resolve process name for pid=%s: %s", value, exc)
            result = ("", value)

    if cache is not None:
        cache[key] = result
    return result


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
        identity_cache: dict[int, tuple[str, int]] = {}
        for control in auto.GetRootControl().GetChildren():
            try:
                if control.ControlType != auto.ControlType.WindowControl:
                    continue
                hwnd = int(control.NativeWindowHandle)
                process_name, pid = _process_identity(hwnd, cache=identity_cache)
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
                        hwnd=hwnd,
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
            _win32_enum_windows(summaries, seen, cache=identity_cache)
        except Exception as exc:  # noqa: BLE001
            _log.debug("list_windows win32 fallback failed: %s", exc)
        return summaries
    except Exception as exc:  # noqa: BLE001 — UIA can refuse to initialise
        _log.debug("list_windows failed: %s", exc)
        return []


def _win32_enum_windows(
    summaries: list[WindowSummary],
    seen: set[int],
    *,
    cache: dict[int, tuple[str, int]] | None = None,
) -> None:
    """Supplement ``list_windows`` with minimized top-level windows.

    ``uiautomation``'s walker does not yield iconic (minimized) windows, so
    screenshot / click tools can't address them. A raw ``EnumWindows`` pass
    includes those, so we add any hwnd the UIA pass missed. Same safety
    filters (visible-process + title-blocklist) apply as the UIA path.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    # Same reason as in _process_identity: default c_int argument
    # conversion would truncate wide HWNDs and read the wrong window.
    get_text_length = user32.GetWindowTextLengthW
    get_text_length.argtypes = [wintypes.HWND]
    get_text_length.restype = ctypes.c_int
    get_text = user32.GetWindowTextW
    get_text.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    get_text.restype = ctypes.c_int
    is_visible = user32.IsWindowVisible
    is_visible.argtypes = [wintypes.HWND]
    is_visible.restype = wintypes.BOOL
    enum_windows = user32.EnumWindows
    enum_windows.argtypes = [wintypes.WNDENUMPROC, wintypes.LPARAM]
    enum_windows.restype = wintypes.BOOL

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd, _lparam):
        try:
            if int(hwnd) in seen:
                return True
            if not is_visible(hwnd):
                return True
            length = get_text_length(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            get_text(hwnd, buf, length + 1)
            title = buf.value or ""
            if title_blocked(title):
                return True
            process_name, pid = _process_identity(int(hwnd), cache=cache)
            if process_name and not is_visible_process(process_name):
                return True
            summaries.append(
                WindowSummary(
                    process=process_name,
                    pid=pid,
                    title=title,
                    hwnd=int(hwnd),
                )
            )
            seen.add(int(hwnd))
        except Exception:  # noqa: BLE001 — never crash on a bad window
            pass
        return True

    enum_windows(_callback, 0)


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


def describe(*, hwnd: int) -> WindowSummary | None:
    """Resolve a live ``hwnd`` to its window metadata, or ``None``.

    Reads the same :func:`list_windows` pass ``ui.list_windows`` exposes,
    so a window can never be described one way by one tool and another
    way by the next. ``None`` means the handle is not (or no longer) a
    listable top-level window — minimized-and-hidden windows are included,
    credential windows are filtered out by :func:`list_windows`.
    """
    for summary in list_windows():
        if int(summary.hwnd) == int(hwnd):
            return summary
    return None


def _now_ms() -> int:
    return int(time.time() * 1000)


__all__ = [
    "AuthorizedWindow",
    "DEFAULT_TTL_MS",
    "WindowSummary",
    "authorize",
    "describe",
    "is_authorized",
    "list_windows",
    "lookup",
    "revoke",
]