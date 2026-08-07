"""Tool response envelope — the contract every tool returns.

This module is **frozen** for the spike and inherited by every later change.
The shapes below are pinned by :mod:`localmcptools.persistence.audit`'s
schema (meta fields are stored as columns) and by the requirements in
``openspec/changes/bootstrap-mcp-server/specs/workspace-inspect-stub.md``.

Two rules that must not regress:

1. **Errors do not leak Python internals.** When a tool raises, the server
   must convert the exception into a :class:`ToolResponse` with
   ``ok=False`` and an :class:`ToolError` whose ``code`` is from
   :data:`STANDARD_ERROR_CODES`. ``error_message`` is a short, redacted
   string suitable for the agent's context.

2. **New error codes are added here, not invented per tool.** If a tool
   needs a new failure mode, extend :data:`STANDARD_ERROR_CODES` and
   document it; don't write a brand-new string into ``error.code``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# --- Standard error code registry -----------------------------------------
#
# Frozen for the spike. Every tool's failure path must pick a code from
# this set. Additions go in their own change with a migration note.

ERR_INTERNAL_ERROR = "internal_error"
ERR_INVALID_ARGS = "invalid_args"
ERR_NOT_IMPLEMENTED = "not_implemented"
ERR_APPROVAL_REQUIRED = "approval_required"

# Added by change-2 (core-shell-and-audit). See
# openspec/changes/core-shell-and-audit/specs/environment-and-workspace.md.
ERR_INVALID_PATH = "invalid_path"
ERR_WORKSPACE_NOT_REGISTERED = "workspace_not_registered"
ERR_ARTIFACT_NOT_FOUND = "artifact_not_found"
ERR_REDACTION_FAILED = "redaction_failed"
ERR_BINARY_FILE = "binary_file"

STANDARD_ERROR_CODES: tuple[str, ...] = (
    ERR_INTERNAL_ERROR,
    ERR_INVALID_ARGS,
    ERR_NOT_IMPLEMENTED,
    ERR_APPROVAL_REQUIRED,
    ERR_INVALID_PATH,
    ERR_WORKSPACE_NOT_REGISTERED,
    ERR_ARTIFACT_NOT_FOUND,
    ERR_REDACTION_FAILED,
    ERR_BINARY_FILE,
)


# --- Envelope models ------------------------------------------------------
#
# Match the openspec design exactly. Any new optional field must have a
# default so older test fixtures keep working.


class ToolMeta(BaseModel):
    """Operational metadata attached to every tool response."""

    model_config = ConfigDict(extra="forbid")

    tool: str
    duration_ms: int = Field(ge=0)
    audit_id: str
    log_path: str | None = None
    run_id: str
    output_handle: str | None = None
    # ``workspace_id`` is non-null for any tool that operates on a
    # registered workspace. ``None`` is the spike-default for tools
    # that don't need a workspace (e.g. ``environment.get``).
    workspace_id: str | None = None
    # REQ-OUT-2: ``output.tail`` returns the same handle on
    # ``meta.evidence_handle`` so the agent can confirm it is paging
    # the artifact produced by the originating call (idempotent).
    evidence_handle: str | None = None
    # Hints the agent can act on without re-asking. Free-form strings;
    # stable labels ("show_audit", "open_artifact") are preferred.
    next_actions: list[str] = Field(default_factory=list)


class ToolError(BaseModel):
    """Failure description; ``code`` is from STANDARD_ERROR_CODES."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    suggestion: str | None = None
    blocked_by: str | None = None  # e.g. "policy", "approval", "dangerous"
    severity: str | None = None    # e.g. "info", "warning", "critical"
    approval_id: str | None = None


class ToolResponse(BaseModel):
    """What every tool returns to the agent."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    data: Any | None = None
    meta: ToolMeta
    error: ToolError | None = None

    # --- Convenience builders -------------------------------------------

    @classmethod
    def ok_response(
        cls,
        *,
        data: Any,
        meta: ToolMeta,
    ) -> ToolResponse:
        """Build a successful response."""
        return cls(ok=True, data=data, meta=meta, error=None)

    def to_envelope(
        self,
        *,
        suggestion: str | None = None,
        blocked_by: str | None = None,
        severity: str | None = None,
        approval_id: str | None = None,
    ) -> ToolResponse:
        """Attach error-context fields and return ``self``.

        Used by the :func:`fail` helper to materialise an error
        response with a fully-built ``meta``. Keeps the call-site
        short while preserving the strict ``code`` registry.
        """
        if self.error is not None:
            if suggestion is not None:
                self.error.suggestion = suggestion
            if blocked_by is not None:
                self.error.blocked_by = blocked_by
            if severity is not None:
                self.error.severity = severity
            if approval_id is not None:
                self.error.approval_id = approval_id
        return self

    @classmethod
    def error_response(
        cls,
        *,
        code: str,
        message: str,
        meta: ToolMeta,
        suggestion: str | None = None,
        blocked_by: str | None = None,
        severity: str | None = None,
        approval_id: str | None = None,
    ) -> ToolResponse:
        """Build a failure response. ``code`` must be from STANDARD_ERROR_CODES."""
        if code not in STANDARD_ERROR_CODES:
            # Catch obvious misuse early; this is a programmer error.
            raise ValueError(
                f"ToolResponse.error_response: unknown error code {code!r}; "
                f"add it to STANDARD_ERROR_CODES first."
            )
        return cls(
            ok=False,
            data=None,
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
