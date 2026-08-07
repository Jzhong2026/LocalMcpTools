"""``workspace.inspect`` stub for the bootstrap spike.

This is the *only* tool exposed by the spike server. The full version lands
in change-2 (``core-shell-and-audit``). Behaviour right now:

- No inputs are validated beyond ``placeholder`` being optional.
- Returns ``{pid, build: "spike-0"}`` wrapped in the standard envelope.
- Records a row in ``audit.sqlite`` for every call (start + finish).
- The profile is **always** ``observe`` during the spike; we never
  escalate here. Real profiles arrive in change-3.

The function is intentionally small. The point of the spike is to prove
that the MCP SDK, the envelope, and the audit pipeline all wire together
correctly through stdio — not to ship a useful tool.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..persistence import audit
from ._common import ToolMeta, ToolResponse

# Spike version. Bumped only when the envelope or audit shape changes.
SPIKE_BUILD = "spike-0"

# Default profile for every tool in the spike. ``observe`` is read-only.
SPIKE_PROFILE = "observe"

# Policy version. Same value across the whole spike — change-3 will
# promote this to a real versioned policy.
SPIKE_POLICY_VERSION = "spike-0"


class WorkspaceInspectResult(BaseModel):
    """Spike payload for ``workspace.inspect``."""

    model_config = ConfigDict(extra="forbid")

    pid: int
    build: str


def inspect_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Implement ``workspace.inspect``.

    ``args`` is whatever the MCP client sends. The spike accepts (and
    ignores) a ``placeholder`` field so tests can prove round-trip with
    non-empty input.

    Returns the dict serialised by the MCP SDK; the tool itself never
    touches the audit row directly — the server wraps the call in
    :func:`invoke` which handles recording.
    """
    # Surface the field so it doesn't look like we're ignoring input.
    _ = args.get("placeholder", None)

    return WorkspaceInspectResult(
        pid=os.getpid(),
        build=SPIKE_BUILD,
    ).model_dump()


# --- Audit-wrapped invocation --------------------------------------------
#
# FastMCP tools are just functions registered via ``@mcp.tool()``. They
# can't easily inject cross-cutting audit recording without a wrapper.
# This helper is the single entry-point that any tool uses to wrap its
# raw logic so audit rows are always written.
#
# Returned dict shape matches the spec:
#     {ok, data, meta, error}


def invoke(tool_name: str, args: dict[str, Any], logic: Any) -> dict[str, Any]:
    """Run ``logic`` and wrap its result in the envelope + audit.

    ``logic`` is a callable ``(args: dict) -> Any`` returning either:
    - a JSON-serialisable value → wrapped in a successful response
    - a :class:`ToolResponse` → returned as-is

    Exceptions are converted to ``internal_error`` responses; their
    message is preserved (one-line) but no traceback leaks into JSON.
    """
    call_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    started = time.monotonic()

    audit.record_start(
        call_id=call_id,
        tool=tool_name,
        args_redacted=args,
        run_id=run_id,
        profile=SPIKE_PROFILE,
        policy_version=SPIKE_POLICY_VERSION,
        agent=None,  # populated in change-3 once the agent advertises itself
        pid=os.getpid(),
    )

    try:
        result = logic(args)
    except Exception as exc:  # noqa: BLE001 — must catch everything to keep the envelope clean
        duration_ms = int((time.monotonic() - started) * 1000)
        msg = _safe_message(exc)
        audit.record_finish(
            call_id,
            ok=False,
            error_code="internal_error",
            error_message=msg,
            duration_ms=duration_ms,
        )
        meta = _meta(tool_name, call_id, run_id, duration_ms)
        return ToolResponse.error_response(
            code="internal_error",
            message=msg,
            meta=meta,
            suggestion="check server logs",
            severity="critical",
        ).model_dump()

    duration_ms = int((time.monotonic() - started) * 1000)
    audit.record_finish(
        call_id,
        ok=True,
        error_code=None,
        error_message=None,
        duration_ms=duration_ms,
    )

    if isinstance(result, ToolResponse):
        # Tool returned a fully built response (e.g. invalid_args path).
        # We still want to update meta to reflect the actual duration.
        result.meta.duration_ms = duration_ms
        result.meta.audit_id = call_id
        return result.model_dump()

    meta = _meta(tool_name, call_id, run_id, duration_ms)
    return ToolResponse.ok_response(data=result, meta=meta).model_dump()


def _meta(tool: str, audit_id: str, run_id: str, duration_ms: int) -> ToolMeta:
    return ToolMeta(
        tool=tool,
        duration_ms=duration_ms,
        audit_id=audit_id,
        run_id=run_id,
    )


def _safe_message(exc: BaseException) -> str:
    """One-line, redacted-safe message. No traceback."""
    name = type(exc).__name__
    text = str(exc).strip()
    if not text:
        return name
    # Limit length so a hostile agent payload can't blow up context.
    return f"{name}: {text[:200]}"
