"""Atomic action + verification wrapper.

The MCP-level wrapper for the agent — performs the action, runs the
verification, and returns a single combined result. The tool body in
:mod:`localmcptools.tools.ui` records one audit row for the whole
sequence, satisfying REQ-UI-5 / REQ-UI-6 (``action fail + verify fail
both recorded``).

The action types supported today:

- ``click`` — :func:`localmcptools.ui.actions.click` at window-local
  coordinates.
- ``type_text`` — :func:`localmcptools.ui.actions.type_text`.

Verification predicates come from :mod:`.verify`.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from . import actions, verify
from .windows import lookup

_log = logging.getLogger(__name__)


def act_and_verify(
    *,
    action_type: str,
    action_args: dict[str, Any],
    predicates: list[verify.Predicate] | None = None,
) -> dict[str, Any]:
    """Perform an action + verify. Single audit row + combined result.

    Returns ``{run_id, action: {...}, verification: {...}, passed: bool}``.
    """
    run_id = uuid.uuid4().hex

    window_id = action_args.get("window_id")
    if not window_id:
        return {"error": {"code": "window_id_required", "message": "action_args.window_id is required"}}
    if lookup(window_id=str(window_id)) is None:
        return {"error": {"code": "window_not_authorized", "message": "window must be authorised first"}}

    started = time.time()
    if action_type == "click":
        action_result = actions.click(
            window_id=str(window_id),
            x=int(action_args.get("x", 0)),
            y=int(action_args.get("y", 0)),
            button=str(action_args.get("button", "left")),
        )
    elif action_type == "type_text":
        action_result = actions.type_text(
            window_id=str(window_id),
            text=str(action_args.get("text", "")),
            interval_ms=int(action_args.get("interval_ms", 0)),
        )
    else:
        return {
            "run_id": run_id,
            "passed": False,
            "action": {"error": f"unsupported action_type {action_type!r}"},
            "verification": {"passed": False, "predicates": [], "summary": "skipped"},
            "next_actions": ["use action_type=click or action_type=type_text"],
        }

    verification = verify.verify(predicates=predicates or [])
    passed = ("error" not in action_result) and bool(verification.get("passed"))
    return {
        "run_id": run_id,
        "action": action_result,
        "verification": verification,
        "passed": passed,
        "duration_ms": int((time.time() - started) * 1000),
    }


__all__ = ["act_and_verify"]