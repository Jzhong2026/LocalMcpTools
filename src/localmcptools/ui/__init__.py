"""UI Automation layer (change-6).

This package contains every Windows UI Automation (UIA) interaction —
window enumeration, tree walking, element finding, screenshot capture,
action dispatch, and verification. The OCR provider interface lives in
:mod:`localmcptools.ui.ocr`; the tool bodies that surface them to MCP
live in :mod:`localmcptools.tools.ui` and :mod:`localmcptools.tools.ocr`.

On non-Windows platforms the public functions degrade gracefully:
:func:`windows.list_windows` returns ``[]`` and the tree/click tools
return ``{"error": {"code": "platform_unsupported"}}``. The design
intent is that the agent never has to special-case the host OS — it
just sees a clean error envelope.
"""

from __future__ import annotations

from . import actions, find, ocr, screens, tree, verify, windows
from .act_and_verify import act_and_verify

__all__ = [
    "act_and_verify",
    "actions",
    "find",
    "ocr",
    "screens",
    "tree",
    "verify",
    "windows",
]