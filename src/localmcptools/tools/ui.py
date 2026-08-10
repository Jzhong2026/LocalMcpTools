"""``ui.*`` MCP tools — UIA-driven UI automation.

The surface is intentionally small:

- :func:`ui_get_ui_tree` — return the structured UIA tree for an
  authorised window. Spills to an artifact when the tree exceeds 500
  nodes (per REQ-UI-2).

- :func:`ui_find_element` — search by text / automationId /
  controlType / name with AND semantics.

- :func:`ui_screenshot_window` / :func:`ui_screenshot_full` /
  :func:`ui_screenshot_region` — capture pixels to an artifact
  handle. Per-agent rate-limited to 20 / minute.

- :func:`ui_click_element` — click at a (x, y) inside the window.
  Requires ``verify_with`` (one or more verification predicates);
  records a single audit row.

- :func:`ui_type_text` — focus the window and type. Requires
  ``verify_with`` like the click tool.

- :func:`ui_act_and_verify` — wrapper that records one audit row for
  the whole action + verification sequence.

All read-only tools (``get_ui_tree`` / ``find_element`` /
``screenshot_*``) require only the ``observe`` profile. Anything that
sends input (``click_element`` / ``type_text`` / ``act_and_verify``)
requires the ``interactive_ui`` profile + an approval per the policy
matrix.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from ..policy.approval import (
    ApprovalDigestMismatch,
    ApprovalExpired,
    ApprovalNotApproved,
    consume,
    request,
)
from ..policy.authorize import Decision, check
from ..policy.digest import digest_for
from ..policy.profile import current
from ..ui import actions, screens, tree
from ..ui.verify import (
    OCRPredicate,
    Predicate,
    ScreenshotPredicate,
    UIAPredicate,
)
from ..ui.windows import authorize, is_authorized, list_windows, revoke
from ..workspaces.registry import WorkspaceNotRegistered, resolve
from ._common import ToolMeta, ToolResponse
from ._errors import fail

_log = logging.getLogger(__name__)


# --- Window management ----------------------------------------------------


def ui_list_windows(args: dict[str, Any]) -> Any:
    """List visible top-level windows (credential windows filtered out)."""
    return {"windows": [w.__dict__ for w in list_windows()]}


def ui_authorize_window(args: dict[str, Any]) -> Any:
    """Authorize a window for subsequent ui.* tool calls."""
    hwnd = args.get("hwnd")
    if not isinstance(hwnd, int):
        return {"error": {"code": "invalid_args", "message": "hwnd must be an integer"}}
    title = str(args.get("title", ""))
    process = str(args.get("process", ""))
    pid = int(args.get("pid", 0))
    ttl_ms = int(args.get("ttl_ms", 60 * 60 * 1000))
    row = authorize(hwnd=hwnd, process=process, pid=pid, title=title, ttl_ms=ttl_ms)
    return {"window": row.__dict__}


def ui_revoke_window(args: dict[str, Any]) -> Any:
    """Revoke an authorised window."""
    window_id = args.get("window_id")
    if not isinstance(window_id, str) or not window_id:
        return {"error": {"code": "invalid_args", "message": "window_id is required"}}
    return {"revoked": revoke(window_id=window_id)}


# --- Read-only tools ------------------------------------------------------


def ui_get_ui_tree(args: dict[str, Any]) -> Any:
    window_id = args.get("window_id")
    if not isinstance(window_id, str) or not window_id:
        return {"error": {"code": "invalid_args", "message": "window_id is required"}}
    if not is_authorized(window_id=window_id):
        return {"error": {"code": "window_not_authorized", "message": "call ui.authorize_window first"}}
    depth = int(args.get("depth", 4))
    from ..ui.windows import lookup

    row = lookup(window_id=window_id)
    if row is None:
        return {"error": {"code": "window_not_authorized"}}
    return tree.get_tree(hwnd=row.hwnd, depth=depth)


def ui_find_element(args: dict[str, Any]) -> Any:
    window_id = args.get("window_id")
    if not isinstance(window_id, str) or not window_id:
        return {"error": {"code": "invalid_args", "message": "window_id is required"}}
    if not is_authorized(window_id=window_id):
        return {"error": {"code": "window_not_authorized"}}
    from ..ui.find import find_element

    return {"matches": find_element(
        window_id=window_id,
        text=args.get("text"),
        automation_id=args.get("automationId"),
        control_type=args.get("controlType"),
        name=args.get("name"),
        max_results=int(args.get("max_results", 20)),
    )}


def ui_screenshot_window(args: dict[str, Any]) -> Any:
    window_id = args.get("window_id")
    if not isinstance(window_id, str) or not window_id:
        return {"error": {"code": "invalid_args", "message": "window_id is required"}}
    if not is_authorized(window_id=window_id):
        return {"error": {"code": "window_not_authorized"}}
    from ..ui.windows import lookup

    row = lookup(window_id=window_id)
    if row is None:
        return {"error": {"code": "window_not_authorized"}}
    return screens.capture(
        mode="window", hwnd=row.hwnd, rate_key=f"window:{window_id}",
    )


def ui_screenshot_full(args: dict[str, Any]) -> Any:
    return screens.capture(mode="full", rate_key="full")


def ui_screenshot_region(args: dict[str, Any]) -> Any:
    region = args.get("region") or {}
    if not isinstance(region, dict):
        return {"error": {"code": "invalid_args", "message": "region must be an object"}}
    return screens.capture(mode="region", region=region, rate_key="region")


# --- Side-effect tools ----------------------------------------------------


def _require_interactive_ui(tool: str, args: dict[str, Any]) -> tuple[Any, Any, str, str] | Any:
    """Common pre-flight for side-effect UI tools.

    Returns ``(workspace_id, workspace_or_None, profile, capability)``
    on success or an error dict.
    """
    workspace_id = args.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id:
        fail(code="invalid_args", message="workspace_id is required", tool=tool, audit_id="pending", run_id="pending")
    workspace_id = cast(str, workspace_id)
    try:
        workspace = resolve(workspace_id)
    except WorkspaceNotRegistered as exc:
        fail(code="workspace_not_registered", message=str(exc), tool=tool, audit_id="pending", run_id="pending")
    profile = current(workspace.id)
    capability = f"{profile.value}:{tool}"
    if check(profile, capability) is Decision.DENY:
        fail(
            code="insufficient_capability",
            message="workspace profile cannot drive UI input",
            tool=tool, audit_id="pending", run_id="pending",
            workspace_id=workspace.id,
            suggestion="an operator must grant interactive_ui through the approval authority",
        )
    return workspace.id, workspace, profile.value, capability


def _build_predicates(args: dict[str, Any]) -> list[Predicate]:
    """Translate ``verify_with`` (list of dicts) into typed predicates."""
    items = args.get("verify_with")
    if not items or not isinstance(items, list):
        return []
    predicates: list[Predicate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind", "uia")
        if kind == "uia":
            predicates.append(
                UIAPredicate(
                    window_id=str(item.get("window_id") or args.get("window_id")),
                    criterion=item.get("criterion", {}),
                    expected=item.get("expected", {}),
                )
            )
        elif kind == "screenshot":
            predicates.append(
                ScreenshotPredicate(
                    window_id=int(item.get("hwnd") or 0),
                    reference_handle=str(item.get("reference_handle", "")),
                    threshold=float(item.get("threshold", 0.02)),
                )
            )
        elif kind == "ocr":
            predicates.append(
                OCRPredicate(
                    window_id=str(item.get("window_id") or args.get("window_id")),
                    expected=str(item.get("expected", "")),
                    match=str(item.get("match", "contains")),
                )
            )
    return predicates


def ui_click_element(args: dict[str, Any]) -> ToolResponse:
    tool = "ui.click_element"
    pre = _require_interactive_ui(tool, args)
    if not isinstance(pre, tuple):
        return pre  # type: ignore[return-value]
    workspace_id, _workspace, profile_value, capability = pre
    if not is_authorized(window_id=str(args.get("window_id", ""))):
        fail(code="window_not_authorized", message="call ui.authorize_window first", tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace_id)
    verify_with = args.get("verify_with")
    if not verify_with:
        fail(code="verification_required", message="ui.click_element requires verify_with", tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace_id)
    # Approval gate.
    approval_id = args.get("approval_id")
    digest = digest_for(tool, args, workspace_id, profile_value)
    if not isinstance(approval_id, str) or not approval_id:
        approval = request(workspace_id, capability, args, profile=profile_value)
        fail(
            code="approval_required",
            message="human approval required before clicking",
            tool=tool, audit_id="pending", run_id="pending",
            workspace_id=workspace_id, approval_id=approval.id,
            data={"window_id": args.get("window_id"), "x": args.get("x"), "y": args.get("y")},
        )
    approval_id = cast(str, approval_id)
    try:
        consume(approval_id, digest)
    except ApprovalDigestMismatch as exc:
        fail(code="approval_digest_mismatch", message=str(exc), tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace_id)
    except ApprovalExpired as exc:
        fail(code="approval_expired", message=str(exc), tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace_id)
    except ApprovalNotApproved:
        fail(code="approval_required", message="approval is still pending", tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace_id, approval_id=approval_id)
    # Execute.
    from ..ui.act_and_verify import act_and_verify

    result = act_and_verify(
        action_type="click",
        action_args={
            "window_id": str(args.get("window_id")),
            "x": int(args.get("x", 0)),
            "y": int(args.get("y", 0)),
            "button": str(args.get("button", "left")),
        },
        predicates=_build_predicates(args),
    )
    meta = ToolMeta(tool=tool, duration_ms=int(result.get("duration_ms", 0)),
                     audit_id="pending", run_id=result.get("run_id", ""),
                     workspace_id=workspace_id)
    if not result.get("passed"):
        return ToolResponse.error_response(code="verification_failed", message="click did not pass verification", meta=meta, data=result)
    return ToolResponse.ok_response(data=result, meta=meta)


def ui_type_text(args: dict[str, Any]) -> ToolResponse:
    tool = "ui.type_text"
    pre = _require_interactive_ui(tool, args)
    if not isinstance(pre, tuple):
        return pre  # type: ignore[return-value]
    workspace_id, _workspace, profile_value, capability = pre
    if not is_authorized(window_id=str(args.get("window_id", ""))):
        fail(code="window_not_authorized", message="call ui.authorize_window first", tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace_id)
    if not args.get("verify_with"):
        fail(code="verification_required", message="ui.type_text requires verify_with", tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace_id)
    text = args.get("text", "")
    if not isinstance(text, str) or not text:
        fail(code="invalid_args", message="text is required", tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace_id)
    approval_id = args.get("approval_id")
    digest = digest_for(tool, args, workspace_id, profile_value)
    if not isinstance(approval_id, str) or not approval_id:
        approval = request(workspace_id, capability, args, profile=profile_value)
        fail(code="approval_required", message="human approval required before typing", tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace_id, approval_id=approval.id)
    approval_id = cast(str, approval_id)
    try:
        consume(approval_id, digest)
    except ApprovalDigestMismatch as exc:
        fail(code="approval_digest_mismatch", message=str(exc), tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace_id)
    except ApprovalExpired as exc:
        fail(code="approval_expired", message=str(exc), tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace_id)
    except ApprovalNotApproved:
        fail(code="approval_required", message="approval is still pending", tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace_id, approval_id=approval_id)
    from ..ui.act_and_verify import act_and_verify

    result = act_and_verify(
        action_type="type_text",
        action_args={
            "window_id": str(args.get("window_id")),
            "text": text,
            "interval_ms": int(args.get("interval_ms", 0)),
        },
        predicates=_build_predicates(args),
    )
    meta = ToolMeta(tool=tool, duration_ms=int(result.get("duration_ms", 0)),
                     audit_id="pending", run_id=result.get("run_id", ""),
                     workspace_id=workspace_id)
    if not result.get("passed"):
        return ToolResponse.error_response(code="verification_failed", message="type_text did not pass verification", meta=meta, data=result)
    return ToolResponse.ok_response(data=result, meta=meta)


def ui_act_and_verify(args: dict[str, Any]) -> ToolResponse:
    """Generic action+verify wrapper for advanced cases.

    The agent passes ``action_type`` + ``action_args`` + ``verify_with``.
    Useful when the click / type tools don't quite fit; the action
    itself is still one of ``click | type_text``.
    """
    tool = "ui.act_and_verify"
    pre = _require_interactive_ui(tool, args)
    if not isinstance(pre, tuple):
        return pre  # type: ignore[return-value]
    workspace_id, _workspace, profile_value, capability = pre
    approval_id = args.get("approval_id")
    digest = digest_for(tool, args, workspace_id, profile_value)
    if not isinstance(approval_id, str) or not approval_id:
        approval = request(workspace_id, capability, args, profile=profile_value)
        fail(code="approval_required", message="human approval required", tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace_id, approval_id=approval.id)
    try:
        consume(approval_id, digest)
    except ApprovalDigestMismatch as exc:
        fail(code="approval_digest_mismatch", message=str(exc), tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace_id)
    except ApprovalExpired as exc:
        fail(code="approval_expired", message=str(exc), tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace_id)
    except ApprovalNotApproved:
        fail(code="approval_required", message="approval is still pending", tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace_id, approval_id=approval_id)
    from ..ui.act_and_verify import act_and_verify

    action_type = str(args.get("action_type", "click"))
    action_args = dict(args.get("action_args") or {})
    action_args.setdefault("window_id", str(args.get("window_id", "")))
    result = act_and_verify(
        action_type=action_type,
        action_args=action_args,
        predicates=_build_predicates(args),
    )
    meta = ToolMeta(tool=tool, duration_ms=int(result.get("duration_ms", 0)),
                     audit_id="pending", run_id=result.get("run_id", ""),
                     workspace_id=workspace_id)
    if not result.get("passed"):
        return ToolResponse.error_response(code="verification_failed", message="action did not pass verification", meta=meta, data=result)
    return ToolResponse.ok_response(data=result, meta=meta)


__all__ = [
    "ui_act_and_verify",
    "ui_authorize_window",
    "ui_click_element",
    "ui_find_element",
    "ui_get_ui_tree",
    "ui_list_windows",
    "ui_revoke_window",
    "ui_screenshot_full",
    "ui_screenshot_region",
    "ui_screenshot_window",
    "ui_type_text",
]