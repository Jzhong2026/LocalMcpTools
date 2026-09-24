"""Element finding — REQ-UI-3.

Combines up to four criteria (text / automationId / controlType / name)
with AND semantics. Returns the top 20 matches with a score; the score
is a simple weighted sum (text + name matches weigh more than
automation-id because operators think in visible labels).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from .tree import attach_to_window

_log = logging.getLogger(__name__)

_MAX_RESULTS = 20


@dataclass(frozen=True)
class Match:
    """One element candidate."""

    name: str
    automation_id: str
    control_type: str
    bounding_box: dict[str, int]
    score: int
    is_visible: bool = True


def _visible_flag(control: Any) -> bool:
    """Read ``IsVisible``, treating an unavailable property as visible.

    ``uiautomation`` returns ``None`` when the property is unsupported on
    a host. ``bool(None)`` is ``False``, which would make a perfectly
    good control look hidden — see the identical trap in
    :func:`localmcptools.ui.tree._visible_flag`.
    """
    try:
        raw = getattr(control, "IsVisible", None)
    except Exception:  # noqa: BLE001 — UIA can raise on a dead control
        return True
    if raw is None:
        return True
    return bool(raw)


def find_element(
    *,
    window_id: str,
    text: str | None = None,
    automation_id: str | None = None,
    control_type: str | None = None,
    name: str | None = None,
    max_results: int = _MAX_RESULTS,
) -> list[dict[str, Any]]:
    """Find elements matching the AND combination of criteria.

    Empty / None criteria are skipped. ``text`` is a case-insensitive
    substring; ``name`` is also substring; ``automation_id`` is exact;
    ``control_type`` is exact (case-insensitive).
    """
    if os.name != "nt":
        return []
    root = attach_to_window(window_id=window_id)
    if root is None:
        return []

    text_lower = (text or "").lower()
    name_lower = (name or "").lower()
    auto_id = automation_id or ""
    ctrl_type = (control_type or "").lower()

    results: list[Match] = []
    _walk(root, text_lower, auto_id, ctrl_type, name_lower, results)
    results.sort(key=lambda m: m.score, reverse=True)
    return [
        {
            "name": m.name,
            "automationId": m.automation_id,
            "controlType": m.control_type,
            "boundingBox": m.bounding_box,
            "isVisible": m.is_visible,
            "score": m.score,
        }
        for m in results[:max_results]
    ]


def _walk(
    control: Any,
    text_lower: str,
    auto_id: str,
    ctrl_type: str,
    name_lower: str,
    out: list[Match],
) -> None:
    """Depth-first walker. Populates ``out`` with every candidate.

    Nodes UIA explicitly reports as not visible — which includes WPF
    ``Collapsed`` elements — are not offered as matches, because every
    downstream action (``click`` / ``type``) targets a rendered point and
    a collapsed element has none. Traversal continues through them either
    way, so a collapsed container never hides a visible descendant.
    """
    try:
        c_name = control.Name or ""
        c_auto = getattr(control, "AutomationId", None) or ""
        c_type = control.ControlTypeName or ""
        is_visible = _visible_flag(control)
        score = _score(c_name, c_auto, c_type, text_lower, auto_id, ctrl_type, name_lower)
        if score > 0 and is_visible:
            try:
                rect = control.BoundingRectangle
                box = {
                    "x": int(rect.left),
                    "y": int(rect.top),
                    "width": int(rect.width()),
                    "height": int(rect.height()),
                }
            except Exception:  # noqa: BLE001
                box = {"x": 0, "y": 0, "width": 0, "height": 0}
            out.append(
                Match(
                    name=c_name,
                    automation_id=str(c_auto),
                    control_type=c_type,
                    bounding_box=box,
                    score=score,
                    is_visible=is_visible,
                )
            )
        children = control.GetChildren() or []
        for child in children:
            try:
                _walk(child, text_lower, auto_id, ctrl_type, name_lower, out)
            except Exception:  # noqa: BLE001
                continue
    except Exception as exc:  # noqa: BLE001
        _log.debug("find walker skipped a node: %s", exc)


def _score(
    name: str,
    auto_id: str,
    control_type: str,
    text_lower: str,
    want_auto: str,
    want_ctrl: str,
    want_name: str,
) -> int:
    """Weighted AND score.

    A missing criterion is a no-op (``score`` doesn't change). All
    criteria must hit for the node to score. Weights:

    - ``text`` / ``name`` substring match: 3 each
    - ``automation_id`` exact match: 2
    - ``control_type`` exact (case-insensitive) match: 1
    """
    score = 0
    if text_lower and text_lower in name.lower():
        score += 3
    elif text_lower and re.search(text_lower, name.lower(), flags=re.IGNORECASE):
        score += 3
    if want_name and want_name in name.lower():
        score += 3
    if want_auto and want_auto == str(auto_id):
        score += 2
    if want_ctrl and want_ctrl == control_type.lower():
        score += 1
    if score == 0 and not (text_lower or want_auto or want_ctrl or want_name):
        # No criteria → don't match anything (avoids accidentally
        # returning every node when the caller forgot to pass them).
        return 0
    return score


__all__ = ["Match", "find_element"]