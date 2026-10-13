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
import os
import tempfile
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
from ..ui.windows import authorize, describe, is_authorized, list_windows, revoke
from ..workspaces.registry import WorkspaceNotRegistered, resolve
from ._common import STANDARD_ERROR_CODES, ToolMeta, ToolResponse
from ._errors import fail

_log = logging.getLogger(__name__)


# --- Window management ----------------------------------------------------


def ui_list_windows(args: dict[str, Any]) -> Any:
    """List visible top-level windows (credential windows filtered out)."""
    return {"windows": [w.__dict__ for w in list_windows()]}


def _coerce_int(value: Any) -> int | None:
    """Best-effort int coercion for a value that may arrive as a string.

    The tool schema now advertises ``integer`` for these params, so a
    spec-compliant MCP client sends a real JSON number. Some clients,
    however, stringify every field. Rather than failing the call with
    a hard ``invalid_args``, accept a clean numeric string here.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text and (text.isdigit() or (text.startswith("-") and text[1:].isdigit())):
            return int(text)
    return None


def _opt_str(value: Any) -> str | None:
    """Return a non-blank string, or ``None`` for absent / unusable.

    The synthesised MCP wrapper forwards every declared parameter, so an
    omitted argument arrives as an explicit ``None``. ``args.get(k, "")``
    does not help — the key is present, so the default is never used and
    ``str(None)`` produced the literal metadata ``"None"``.
    """
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def ui_authorize_window(args: dict[str, Any]) -> Any:
    """Authorize a window for subsequent ui.* tool calls."""
    hwnd = _coerce_int(args.get("hwnd"))
    if hwnd is None:
        fail(
            code="invalid_args",
            message="hwnd must be an integer",
            tool="ui.authorize_window",
            audit_id="pending",
            run_id="pending",
        )
    title = _opt_str(args.get("title"))
    process = _opt_str(args.get("process"))
    pid = _coerce_int(args.get("pid")) or 0

    # Backfill whatever the caller left out from the live window, so the
    # row this tool writes agrees with `ui.list_windows` for the same
    # hwnd. Callers routinely pass only `hwnd`; storing empty metadata
    # then made the two tools contradict each other.
    summary = describe(hwnd=hwnd)
    if summary is not None:
        if process is None:
            process = summary.process
        if not pid:
            pid = summary.pid
        if title is None:
            title = summary.title

    row = authorize(
        hwnd=hwnd,
        process=process or "",
        pid=pid,
        title=title or "",
        ttl_ms=_coerce_int(args.get("ttl_ms")) or 60 * 60 * 1000,
    )
    return {"window": row.__dict__}


def ui_revoke_window(args: dict[str, Any]) -> Any:
    """Revoke an authorised window."""
    window_id = args.get("window_id")
    if not isinstance(window_id, str) or not window_id:
        fail(
            code="invalid_args",
            message="window_id is required",
            tool="ui.revoke_window",
            audit_id="pending",
            run_id="pending",
        )
    return {"revoked": revoke(window_id=window_id)}


# --- Read-only tools ------------------------------------------------------


def _require_window(args: dict[str, Any], *, tool: str) -> str:
    """Validate ``window_id`` and that it is still authorised."""
    window_id = args.get("window_id")
    if not isinstance(window_id, str) or not window_id:
        fail(
            code="invalid_args",
            message="window_id is required",
            tool=tool,
            audit_id="pending",
            run_id="pending",
        )
    if not is_authorized(window_id=window_id):
        fail(
            code="window_not_authorized",
            message="call ui.authorize_window first",
            tool=tool,
            audit_id="pending",
            run_id="pending",
        )
    return window_id


#: Error slugs that older ``ui.*`` modules spoke as bare strings before
#: the codebase standardised on ``{"error": {"code", "message"}}``. Kept so
#: a straggler module still produces a *useful* code instead of being
#: flattened to ``internal_error`` with a useless message.
_LEGACY_ERROR_CODES: dict[str, str] = {
    "rate_limit": "rate_limit_exceeded",
    "platform_unsupported": "platform_unsupported",
    "invalid_path": "invalid_path",
    "window_not_authorized": "window_not_authorized",
    "workspace_not_registered": "workspace_not_registered",
    "capture_failed": "capture_failed",
    "hwnd_required": "invalid_args",
    "region_required": "invalid_args",
    "invalid_mode": "invalid_args",
    "pillow_not_installed": "internal_error",
    "artifact_failed": "internal_error",
    "save_failed": "internal_error",
    "window_not_found": "capture_failed",
}


def _raise_if_failed(result: Any, *, tool: str) -> None:
    """Turn a module-layer ``error`` payload into a real ``ok: false``.

    Understands both dialects the ``ui.*`` modules have used:

    - nested (current): ``{"error": {"code": ..., "message": ...}}``
    - flat (legacy): ``{"error": "rate_limit", "code": ..., "message": ...,
      "next_actions": [...]}``

    The legacy branch used to collapse every bare string to
    ``internal_error`` *and throw away the sibling ``message``*, which is
    why ``ui.screenshot_to_file`` used to report a rejected path as
    ``internal_error`` with no explanation. Unknown codes still fall back
    to ``internal_error`` (that is what ``fail()`` accepts) but now keep
    the original slug in the message so the cause stays visible.
    """
    if not isinstance(result, dict):
        return
    error = result.get("error")
    if not error:
        return
    suggestion: str | None = None
    if isinstance(error, str):
        # Legacy flat shape — lift the sibling fields out of the envelope.
        slug = error
        raw_message = str(result.get("message") or slug)
        explicit = result.get("code")
        mapped = _LEGACY_ERROR_CODES.get(slug)
        if explicit:
            code = str(explicit)
        elif mapped:
            code = mapped
        else:
            # Neither an explicit code nor a mapping: `internal_error` is
            # the only thing `fail()` accepts, but downgrading alone would
            # erase the slug and leave the caller with a contentless
            # failure. Keep it in the message.
            code = "internal_error"
            raw_message = f"{slug}: {raw_message}"
        actions = result.get("next_actions") or result.get("suggestion")
        if isinstance(actions, (list, tuple)):
            suggestion = "; ".join(str(a) for a in actions) or None
        elif actions:
            suggestion = str(actions)
        error = {"code": code, "message": raw_message}
    else:
        suggestion = str(error.get("suggestion") or "") or None
    code = str(error.get("code") or "internal_error")
    if code not in STANDARD_ERROR_CODES:
        message = str(error.get("message") or f"{tool} failed")
        raise_value = f"{code}: {message}"
        code = "internal_error"
        error = {"code": code, "message": raise_value}
    fail(
        code=code,
        message=str(error.get("message") or f"{tool} failed"),
        tool=tool,
        audit_id="pending",
        run_id="pending",
        suggestion=suggestion,
    )
    raise AssertionError("unreachable — fail() always raises")


def ui_get_ui_tree(args: dict[str, Any]) -> Any:
    tool = "ui.get_ui_tree"
    window_id = _require_window(args, tool=tool)
    # `depth: 0` is a meaningful request (root node only), so only a
    # genuinely absent value falls back to the default.
    depth_raw = args.get("depth")
    depth = 4 if depth_raw is None else int(depth_raw)
    from ..ui.windows import lookup

    row = lookup(window_id=window_id)
    if row is None:
        fail(
            code="window_not_authorized",
            message="the authorization expired or was revoked",
            tool=tool,
            audit_id="pending",
            run_id="pending",
        )
    result = tree.get_tree(hwnd=row.hwnd, depth=depth)
    _raise_if_failed(result, tool=tool)
    return result


def ui_find_element(args: dict[str, Any]) -> Any:
    tool = "ui.find_element"
    window_id = _require_window(args, tool=tool)
    max_raw = args.get("max_results")
    max_results = 20 if max_raw is None else int(max_raw)
    from ..ui.find import find_element

    return {"matches": find_element(
        window_id=window_id,
        text=args.get("text"),
        automation_id=args.get("automationId"),
        control_type=args.get("controlType"),
        name=args.get("name"),
        max_results=max_results,
    )}


def ui_screenshot_window(args: dict[str, Any]) -> Any:
    tool = "ui.screenshot_window"
    window_id = _require_window(args, tool=tool)
    from ..ui.windows import lookup

    row = lookup(window_id=window_id)
    if row is None:
        fail(
            code="window_not_authorized",
            message="the authorization expired or was revoked",
            tool=tool,
            audit_id="pending",
            run_id="pending",
        )
    result = screens.capture(
        mode="window", hwnd=row.hwnd, rate_key=f"window:{window_id}",
    )
    _raise_if_failed(result, tool=tool)
    return result


def ui_screenshot_full(args: dict[str, Any]) -> Any:
    tool = "ui.screenshot_full"
    result = screens.capture(mode="full", rate_key="full")
    _raise_if_failed(result, tool=tool)
    return result


def ui_screenshot_region(args: dict[str, Any]) -> Any:
    tool = "ui.screenshot_region"
    region = args.get("region")
    if not isinstance(region, dict) or not region:
        fail(
            code="invalid_args",
            message="region must be an object with x, y, width and height",
            tool=tool,
            audit_id="pending",
            run_id="pending",
        )
    result = screens.capture(mode="region", region=region, rate_key="region")
    _raise_if_failed(result, tool=tool)
    return result


def _safe_screenshot_path(raw: Any, workspace_id: str | None) -> str | None:
    """Validate and normalise a screenshot output path.

    Returns the absolute path (ensuring a ``.png`` extension) if it is
    allowed, or ``None`` if it must be rejected.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    # No traversal segments allowed at any point.
    if ".." in raw.replace("\\", "/").split("/"):
        return None
    if not os.path.isabs(raw):
        return None
    try:
        resolved = os.path.realpath(raw)
    except (OSError, ValueError):
        return None
    if not resolved.lower().endswith(".png"):
        resolved += ".png"
    if workspace_id:
        try:
            workspace = resolve(workspace_id)
        except WorkspaceNotRegistered:
            return None
        if not workspace.contains(resolved):
            return None
        return resolved
    # No workspace: confine to the per-user temp sandbox.
    sandbox = os.path.join(tempfile.gettempdir(), "localmcptools")
    try:
        resolved_c = os.path.normcase(os.path.realpath(resolved))
        sandbox_c = os.path.normcase(os.path.realpath(sandbox))
    except (OSError, ValueError):
        return None
    prefix = sandbox_c if sandbox_c.endswith(os.sep) else sandbox_c + os.sep
    if resolved_c != sandbox_c and not resolved_c.startswith(prefix):
        return None
    return resolved


def ui_screenshot_to_file(args: dict[str, Any]) -> Any:
    """Capture the desktop / a window / a region and save it to a PNG file.

    Unlike ``ui.screenshot_*`` (which return an opaque artifact handle),
    this writes the image to ``path`` so it can be opened directly. The
    path is confined to the given ``workspace_id``'s root, or — when no
    workspace is supplied — to ``%TEMP%/localmcptools``.

    Modes (exactly one of ``window_id`` / ``region`` selects the mode;
    neither means full desktop):

    - full desktop (no ``window_id`` / ``region``)
    - a window (``window_id`` from ``ui.authorize_window``)
    - a rectangular ``region`` ``{x, y, width, height}``
    """
    tool = "ui.screenshot_to_file"
    # Resolve the workspace *before* validating the path. The other order
    # reports an unregistered workspace as `invalid_path` with a message
    # about absolute paths and traversal, which sends the caller off
    # fixing a path that was never the problem.
    workspace_id = args.get("workspace_id") or None
    if workspace_id:
        try:
            resolve(workspace_id)
        except WorkspaceNotRegistered as exc:
            fail(
                code="workspace_not_registered",
                message=str(exc),
                tool=tool,
                audit_id="pending",
                run_id="pending",
                suggestion="register the workspace first via workspace.register",
            )
    path = _safe_screenshot_path(args.get("path"), workspace_id)
    if path is None:
        fail(
            code="invalid_path",
            message=(
                "path must be absolute, traversal-free, end in .png, and "
                "live inside the workspace root (or %TEMP%/localmcptools)"
            ),
            tool=tool,
            audit_id="pending",
            run_id="pending",
            suggestion="pass an absolute .png path, or drop workspace_id to write into %TEMP%/localmcptools",
        )

    window_id = args.get("window_id")
    region = args.get("region") or {}
    if isinstance(window_id, str) and window_id:
        window_id = _require_window(args, tool=tool)
        from ..ui.windows import lookup

        row = lookup(window_id=window_id)
        if row is None:
            fail(
                code="window_not_authorized",
                message="the authorization expired or was revoked",
                tool=tool,
                audit_id="pending",
                run_id="pending",
            )
        result = screens.capture_to_file(
            path=path, mode="window", hwnd=row.hwnd, rate_key=f"file:window:{window_id}",
        )
        _raise_if_failed(result, tool=tool)
        return result
    if isinstance(region, dict) and region:
        result = screens.capture_to_file(
            path=path, mode="region", region=region, rate_key="file:region",
        )
        _raise_if_failed(result, tool=tool)
        return result
    result = screens.capture_to_file(path=path, mode="full", rate_key="file:full")
    _raise_if_failed(result, tool=tool)
    return result


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
        approval = request(
            workspace_id, capability, args, profile=profile_value,
            preview={"window_id": args.get("window_id"), "x": args.get("x"), "y": args.get("y")},
        )
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
        approval = request(
            workspace_id, capability, args, profile=profile_value,
            preview={"window_id": args.get("window_id"), "text": text},
        )
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
        approval = request(
            workspace_id, capability, args, profile=profile_value,
            preview={"action_type": args.get("action_type"), "window_id": args.get("window_id")},
        )
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
    "ui_screenshot_to_file",
    "ui_screenshot_window",
    "ui_type_text",
]