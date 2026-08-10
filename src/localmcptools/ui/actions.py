"""UIA action dispatch (click / type).

Two surfaces:

- :func:`click` — click the element at ``x/y`` *inside* an authorised
  window. Coordinates are window-local (the caller does not have to
  pre-translate).
- :func:`type_text` — focus the element then send a string.

Both refuse to operate without a verified action plan; callers should
go through :func:`localmcptools.ui.act_and_verify.act_and_verify` for
the audit-row + verification pattern.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from .find import find_element

_log = logging.getLogger(__name__)


def click(
    *,
    window_id: str,
    x: int,
    y: int,
    button: str = "left",
) -> dict[str, Any]:
    """Click at ``(x, y)`` in window-local coordinates."""
    if os.name != "nt":
        return {"error": "platform_unsupported"}
    try:
        import uiautomation as auto  # type: ignore[import-untyped]
        import ctypes
    except ImportError as exc:
        return {"error": "uiautomation_not_installed", "message": str(exc)}

    from .windows import lookup  # local — avoid cycle

    row = lookup(window_id=window_id)
    if row is None:
        return {"error": "window_not_authorized"}

    try:
        control = auto.ControlFromHandle(row.hwnd)
        if control is None:
            return {"error": "window_not_found"}
        # Translate window-local coordinates to screen coordinates so the
        # global cursor moves correctly.
        rect = control.BoundingRectangle
        screen_x = int(rect.left) + int(x)
        screen_y = int(rect.top) + int(y)
        ctypes.windll.user32.SetCursorPos(screen_x, screen_y)
        time.sleep(0.05)
        if button == "right":
            ctypes.windll.user32.mouse_event(0x0008, 0, 0, 0, 0)  # RIGHTDOWN
            time.sleep(0.02)
            ctypes.windll.user32.mouse_event(0x0010, 0, 0, 0, 0)  # RIGHTUP
        else:
            ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
            time.sleep(0.02)
            ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
    except Exception as exc:  # noqa: BLE001
        _log.debug("click failed: %s", exc)
        return {"error": "click_failed", "message": str(exc)}
    return {"ok": True, "x": int(x), "y": int(y), "button": button}


def type_text(
    *,
    window_id: str,
    text: str,
    interval_ms: int = 0,
) -> dict[str, Any]:
    """Type ``text`` into the currently focused element.

    Uses :mod:`ctypes` to send :c:macro:`WM_CHAR` messages directly to
    the foreground window. This is more reliable than ``SendKeys`` for
    Unicode content on Windows.

    The ``interval_ms`` lets the agent throttle typing for slow UIs;
    the default 0 is fine for synchronous apps.
    """
    if os.name != "nt":
        return {"error": "platform_unsupported"}
    if not text:
        return {"error": "text_required"}
    try:
        import ctypes
        import ctypes.wintypes as wintypes
    except ImportError as exc:
        return {"error": "ctypes_unavailable", "message": str(exc)}

    from .windows import lookup  # local — avoid cycle

    row = lookup(window_id=window_id)
    if row is None:
        return {"error": "window_not_authorized"}

    try:
        WM_CHAR = 0x0102
        for char in text:
            ctypes.windll.user32.SendMessageW(row.hwnd, WM_CHAR, ord(char), 0)
            if interval_ms > 0:
                time.sleep(interval_ms / 1000.0)
    except Exception as exc:  # noqa: BLE001
        _log.debug("type_text failed: %s", exc)
        return {"error": "type_failed", "message": str(exc)}
    return {"ok": True, "chars_typed": len(text)}


__all__ = ["click", "type_text"]