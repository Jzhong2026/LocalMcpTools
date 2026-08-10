"""UIA tree walker.

Two surfaces:

- :func:`get_tree` — walk the tree under a target HWND up to ``depth``,
  return the structured node list per REQ-UI-2.

- :func:`_attach_to_window` — connect ``uiautomation`` to a HWND that
  was authorised via :mod:`.windows`. Used by the click / find tools so
  they operate on the same desktop handle.

Trees larger than 500 nodes spill to an artifact; the caller receives
a summary + handle. The summary is a flat list of nodes flattened from
the tree, suitable for grep but not for full traversal — the agent is
expected to page through the artifact for larger cases.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from ..persistence import artifacts

_log = logging.getLogger(__name__)

# Per REQ-UI-2: trees larger than this spill to an artifact.
_MAX_INLINE_NODES = 500


@dataclass
class UINode:
    """Single UIA node in the structured tree shape."""

    name: str = ""
    automation_id: str = ""
    control_type: str = ""
    bounding_box: dict[str, int] = field(default_factory=dict)
    is_enabled: bool = True
    is_visible: bool = True
    children: list["UINode"] = field(default_factory=list)

    def to_dict(self, *, include_children: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "automationId": self.automation_id,
            "controlType": self.control_type,
            "boundingBox": self.bounding_box,
            "isEnabled": self.is_enabled,
            "isVisible": self.is_visible,
        }
        if include_children and self.children:
            out["children"] = [child.to_dict() for child in self.children]
        return out


def get_tree(*, hwnd: int, depth: int = 4) -> dict[str, Any]:
    """Walk the UIA tree under ``hwnd`` up to ``depth`` levels.

    Returns ``{nodes: [...], truncated: bool, handle: str|None,
    total: int, summary: [...]}``. The :class:`UINode` schema matches
    what the Angular automation page renders.

    On non-Windows hosts returns ``{"error": "platform_unsupported"}``
    so the SPA shows a clean message.
    """
    if os.name != "nt":
        return {"error": "platform_unsupported"}
    try:
        import uiautomation as auto  # type: ignore[import-untyped]
    except ImportError:
        return {"error": "uiautomation_not_installed"}

    try:
        root = auto.ControlFromHandle(hwnd)
        if root is None:
            return {"error": "window_not_found"}
        root_node = _walk(root, depth=depth)
    except Exception as exc:  # noqa: BLE001
        _log.debug("get_tree failed: %s", exc)
        return {"error": "uia_failure", "message": str(exc)}

    flat = list(_iter_nodes(root_node))
    total = len(flat)
    truncated = total > _MAX_INLINE_NODES

    if truncated:
        # Spill the full tree to an artifact; surface only the first
        # ``_MAX_INLINE_NODES`` inline so the agent sees something.
        encoded = json.dumps(root_node.to_dict(), ensure_ascii=False)
        handle = artifacts.write(encoded, sensitive=True)
        summary_nodes = [node.to_dict(include_children=False) for node in flat[:_MAX_INLINE_NODES]]
        return {
            "nodes": summary_nodes,
            "truncated": True,
            "handle": handle,
            "total": total,
            "summary": summary_nodes[:10],
        }

    return {
        "nodes": [node.to_dict() for node in flat],
        "truncated": False,
        "handle": None,
        "total": total,
        "summary": [node.to_dict(include_children=False) for node in flat[:10]],
    }


def _walk(control: Any, *, depth: int) -> UINode:
    """Recursive walker. Builds a :class:`UINode` from a UIA control."""
    try:
        name = control.Name or ""
        automation_id = getattr(control, "AutomationId", None) or ""
        control_type = control.ControlTypeName or ""
        try:
            rect = control.BoundingRectangle
            bounding_box = {
                "x": int(rect.left),
                "y": int(rect.top),
                "width": int(rect.width()),
                "height": int(rect.height()),
            }
        except Exception:  # noqa: BLE001 — UIA can raise on hidden
            bounding_box = {"x": 0, "y": 0, "width": 0, "height": 0}
        is_enabled = bool(getattr(control, "IsEnabled", True))
        is_visible = bool(getattr(control, "IsVisible", True))
        node = UINode(
            name=name,
            automation_id=str(automation_id),
            control_type=control_type,
            bounding_box=bounding_box,
            is_enabled=is_enabled,
            is_visible=is_visible,
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug("UIA node read failed: %s", exc)
        node = UINode()

    if depth > 0:
        try:
            children = control.GetChildren()
        except Exception:  # noqa: BLE001
            children = []
        for child in children or []:
            try:
                if child.IsVisible:
                    node.children.append(_walk(child, depth=depth - 1))
            except Exception:  # noqa: BLE001
                continue
    return node


def _iter_nodes(node: UINode):
    """Flatten the tree so we can count and spill."""
    yield node
    for child in node.children:
        yield from _iter_nodes(child)


def attach_to_window(*, window_id: str):
    """Return the ``uiautomation`` root for an authorised window.

    The caller must check :func:`windows.is_authorized` first; this
    function refuses to operate on a window the operator has not
    explicitly approved.
    """
    from .windows import lookup  # local import to avoid cycle

    row = lookup(window_id=window_id)
    if row is None:
        return None
    if os.name != "nt":
        return None
    try:
        import uiautomation as auto  # type: ignore[import-untyped]
    except ImportError:
        return None
    return auto.ControlFromHandle(row.hwnd)


__all__ = ["UINode", "attach_to_window", "get_tree"]