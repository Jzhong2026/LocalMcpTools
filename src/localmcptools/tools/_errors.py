"""Helpers for tools to raise typed error envelopes.

The :func:`fail` function lets a tool raise into a fully-built
:class:`ToolResponse` with an :class:`ToolError`. :class:`ToolExecutionService`
catches the :class:`_ToolErrorResponse` carrier, refreshes the meta
fields, records the audit row, and returns the envelope.

Use this for any failure path where you want a clean error code from
:data:`STANDARD_ERROR_CODES` instead of letting an ``Exception``
propagate (which would be downgraded to ``internal_error``).
"""

from __future__ import annotations

from typing import Any

from ..execution.tool_error import ToolErrorResponse as _ToolErrorResponse
from ._common import STANDARD_ERROR_CODES, ToolError, ToolMeta, ToolResponse

__all__ = ["fail", "make_error_response"]


def fail(
    *,
    code: str,
    message: str,
    tool: str,
    audit_id: str,
    run_id: str,
    duration_ms: int = 0,
    suggestion: str | None = None,
    blocked_by: str | None = None,
    severity: str | None = None,
    approval_id: str | None = None,
    workspace_id: str | None = None,
    next_actions: list[str] | None = None,
    data: Any = None,
) -> None:
    """Raise a typed error envelope.

    The :class:`ToolExecutionService` catches this and writes the
    audit row. Never returns.
    """
    envelope = make_error_response(
        code=code,
        message=message,
        tool=tool,
        audit_id=audit_id,
        run_id=run_id,
        duration_ms=duration_ms,
        suggestion=suggestion,
        blocked_by=blocked_by,
        severity=severity,
        approval_id=approval_id,
        workspace_id=workspace_id,
        next_actions=next_actions,
        data=data,
    )
    raise _ToolErrorResponse(envelope) from None


def make_error_response(
    *,
    code: str,
    message: str,
    tool: str,
    audit_id: str,
    run_id: str,
    duration_ms: int = 0,
    suggestion: str | None = None,
    blocked_by: str | None = None,
    severity: str | None = None,
    approval_id: str | None = None,
    workspace_id: str | None = None,
    next_actions: list[str] | None = None,
    data: Any = None,
) -> ToolResponse:
    """Build an error envelope (without raising).

    Tests and adapters use this; tools should call :func:`fail`.
    """
    if code not in STANDARD_ERROR_CODES:
        raise ValueError(
            f"unknown error code {code!r}; add it to STANDARD_ERROR_CODES first."
        )
    meta = ToolMeta(
        tool=tool,
        duration_ms=duration_ms,
        audit_id=audit_id,
        run_id=run_id,
        workspace_id=workspace_id,
        next_actions=list(next_actions or []),
    )
    envelope = ToolResponse(
        ok=False,
        data=data,
        meta=meta,
        error=ToolError(
            code=code,
            message=message,
            suggestion=suggestion,
            blocked_by=blocked_by,
            severity=severity,
            approval_id=approval_id,
        ),
    )
    return envelope
