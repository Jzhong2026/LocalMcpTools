"""Single chokepoint for tool execution.

Every MCP tool published by :mod:`localmcptools.server` registers a
:data:`ToolLogic` callable that does only the *business* work
(argument parsing, return value). All cross-cutting concerns — audit
recording, envelope construction, exception → error-envelope mapping,
runtime fields — live here.

Why a chokepoint?

- We can't accidentally skip an audit row. A tool that doesn't go
  through :class:`ToolExecutionService` simply doesn't get registered.
- We can't accidentally leak a Python traceback. The wrapper catches
  :class:`Exception` and converts to ``internal_error`` envelope with a
  redacted, length-capped message.
- The OpenSpec ``observe``-by-default decision lives in exactly one
  place: :attr:`ToolExecutionService.profile`. A tool can't elevate it.
- When change-3 (policy-and-safety) adds real approval gates, they
  plug in here too. Tools stay oblivious.

The class is deliberately *not* a singleton. Tests construct their own
instance with a custom ``audit_path`` so they don't touch the user's
real ``%APPDATA%`` database.
"""

from __future__ import annotations

import inspect
import logging
import os
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..persistence import audit, db
from ..safety.redact import redact
from ..tools._common import ToolMeta, ToolResponse
from .tool_error import ToolErrorResponse as _ToolErrorResponse

_log = logging.getLogger(__name__)


# --- Shared constants ----------------------------------------------------

# The default capability profile applied to every tool that hasn't
# been granted an escalation. ``observe`` is read-only. Real
# escalation arrives in change-3 (policy-and-safety).
DEFAULT_PROFILE = "observe"

# Version tag written into every audit row. Bump when the
# authority model changes so historical rows remain self-describing.
DEFAULT_POLICY_VERSION = "phase-1"

# Maximum length of an exception message we let into the agent's
# context. Anything longer is truncated to keep the wire payload small
# and to keep a hostile agent payload from inflating its own response.
_MAX_ERROR_MESSAGE_LEN = 200


# --- Types ---------------------------------------------------------------


# A tool logic is ``(args) -> result``. It may return either a plain
# JSON-serialisable value (wrapped in :class:`ToolResponse.ok_response`)
# or a fully-built :class:`ToolResponse` (returned as-is, after
# refreshing the meta fields).
ToolLogic = Callable[[dict[str, Any]], Any]


@dataclass
class ToolContext:
    """Per-call context handed to the tool logic.

    Carries fields the audit row needs so the tool doesn't have to
    pass them back out. Populated by :class:`ToolExecutionService.invoke`.
    """

    call_id: str
    run_id: str
    started_monotonic: float
    audit_path: Path
    profile: str
    policy_version: str
    tool: str
    args: dict[str, Any]
    # Optional workspace / client context — set by the registration
    # wrapper, *not* by the agent. The agent can ask for a workspace
    # tool but can't claim a different workspace.
    workspace_id: str | None = None
    client_instance: str | None = None


@dataclass
class _Registration:
    """Internal record for a registered tool.

    Public surface is :class:`ToolExecutionService.register`.
    """

    tool: str
    logic: ToolLogic
    title: str = ""
    description: str = ""
    # If true, the tool resolves a workspace before invoking ``logic``.
    requires_workspace: bool = False
    # Extra fields to attach to the audit row (e.g. agent).
    default_workspace_id: str | None = None
    default_client_instance: str | None = None
    extra_meta: dict[str, Any] = field(default_factory=dict)
    # Names of the keyword arguments the tool body expects. Used by
    # :func:`_make_wrapper` to synthesise a typed signature so FastMCP
    # generates a proper JSON schema. An empty tuple means "body
    # takes a single ``args`` dict"; a non-empty tuple means "body
    # takes those named parameters" (call site passes ``**args``).
    param_names: tuple[str, ...] = ()


# --- The service ---------------------------------------------------------


class ToolExecutionService:
    """Owns tool invocation: registration, audit, envelope.

    Construct one per server boot. Tests construct one per test.
    """

    def __init__(
        self,
        *,
        audit_path: Path | None = None,
        profile: str = DEFAULT_PROFILE,
        policy_version: str = DEFAULT_POLICY_VERSION,
    ) -> None:
        self._audit_path = audit_path
        self._profile = profile
        self._policy_version = policy_version
        self._tools: dict[str, _Registration] = {}

    # -- registration ----------------------------------------------------

    def register(
        self,
        tool: str,
        logic: ToolLogic,
        *,
        title: str = "",
        description: str = "",
        requires_workspace: bool = False,
        default_workspace_id: str | None = None,
        default_client_instance: str | None = None,
        extra_meta: dict[str, Any] | None = None,
        param_names: Sequence[str] | None = None,
    ) -> Callable[..., dict[str, Any]]:
        """Register a tool. Returns a FastMCP-friendly callable.

        The returned callable is what gets handed to
        :func:`mcp.server.fastmcp.FastMCP.tool`. It forwards into
        :meth:`invoke`, which does the audit + envelope work.

        ``param_names`` declares the keyword arguments the tool body
        expects. The wrapper synthesises a typed signature from these
        so FastMCP's schema generator can build a real JSON schema
        (rather than one that demands a single ``kwargs`` blob).

        ``requires_workspace`` is a hint for the UI; the chokepoint
        does NOT enforce it. Tools that genuinely need a workspace
        should call :func:`resolve_workspace` themselves so the error
        envelope stays tool-specific.
        """
        if param_names is None:
            # Best-effort auto-detect from the tool's signature. We
            # treat a single ``args: dict``-shaped parameter (or a
            # bare ``args`` with no annotation, since most tool bodies
            # don't bother typing it) as "no declared fields"; tools
            # that want the wrapper to expose individual fields must
            # pass ``param_names`` explicitly.
            try:
                sig = inspect.signature(logic)
                params_list = [
                    p for p in sig.parameters.values()
                    if p.kind not in (
                        inspect.Parameter.VAR_KEYWORD,
                        inspect.Parameter.VAR_POSITIONAL,
                    )
                ]
                if len(params_list) == 1:
                    only = params_list[0]
                    ann = only.annotation
                    # String annotation — try to resolve via get_type_hints.
                    if isinstance(ann, str):
                        try:
                            ann = inspect.get_annotations(logic).get(only.name, ann)
                        except Exception:
                            ann = ann
                    is_dict_like = (
                        ann is dict
                        or ann is inspect.Parameter.empty
                        or only.name == "args"
                        or (isinstance(ann, str) and ann.startswith("dict"))
                    )
                    if is_dict_like:
                        param_names = ()
                    else:
                        param_names = [only.name]
                else:
                    param_names = [p.name for p in params_list]
            except (TypeError, ValueError):
                param_names = ()
        reg = _Registration(
            tool=tool,
            logic=logic,
            title=title,
            description=description,
            requires_workspace=requires_workspace,
            default_workspace_id=default_workspace_id,
            default_client_instance=default_client_instance,
            extra_meta=dict(extra_meta or {}),
            param_names=tuple(param_names or ()),
        )
        self._tools[tool] = reg
        return _make_wrapper(self, reg)

    def tool_names(self) -> list[str]:
        """Public: list the names of every registered tool."""
        return sorted(self._tools)

    def get_registration(self, tool: str) -> _Registration:
        """Public: fetch the registration record (for the FastMCP layer)."""
        try:
            return self._tools[tool]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {tool!r}") from exc

    def _call_logic(
        self,
        registration: _Registration,
        args: dict[str, Any],
    ) -> Any:
        """Invoke the tool body with ``args=args``.

        Tool bodies in this codebase all take a single ``args`` dict
        parameter — they handle their own argument parsing and
        validation. The :attr:`_Registration.param_names` field is
        *only* used by the FastMCP wrapper to emit a proper JSON
        schema; the chokepoint itself never inspects it for dispatch.
        """
        return registration.logic(args)

    # -- invocation ------------------------------------------------------

    def invoke(
        self,
        registration: _Registration,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Run ``registration.logic`` and wrap the result.

        Always writes both ``record_start`` and ``record_finish`` audit
        rows. Exceptions become ``internal_error`` envelopes. The
        returned dict is what FastMCP serialises to the agent.
        """
        call_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        started = time.monotonic()

        # Make sure the schema exists before the first write — first call
        # on a fresh machine would otherwise crash with "no such table".
        if self._audit_path is not None:
            db.init_db(self._audit_path)

        audit.record_start(
            call_id=call_id,
            tool=registration.tool,
            args_redacted=args,
            run_id=run_id,
            profile=self._profile,
            policy_version=self._policy_version,
            agent=None,  # populated in change-3 when agent self-advertises
            client_instance=registration.default_client_instance,
            workspace_id=registration.default_workspace_id,
            pid=os.getpid(),
            path=self._audit_path,
        )

        try:
            result = self._call_logic(registration, args)
        except _ToolErrorResponse as raised:
            # Tools can raise this to short-circuit into a typed envelope
            # (e.g. invalid_args, workspace_not_registered). The raise
            # happens *before* any business work but after we already
            # inserted the audit row, so we still need to update it.
            envelope = raised.response
            envelope.meta = _meta(registration, call_id, run_id,
                                  int((time.monotonic() - started) * 1000),
                                  extra=registration.extra_meta)
            envelope.meta.audit_id = call_id
            envelope.meta.run_id = run_id
            envelope.meta.tool = registration.tool
            duration_ms = envelope.meta.duration_ms
            audit.record_finish(
                call_id,
                ok=False,
                error_code=envelope.error.code if envelope.error else None,
                error_message=envelope.error.message if envelope.error else None,
                duration_ms=duration_ms,
                path=self._audit_path,
            )
            return envelope.model_dump()
        except Exception as exc:  # noqa: BLE001 — must catch everything
            duration_ms = int((time.monotonic() - started) * 1000)
            msg = _safe_message(exc)
            audit.record_finish(
                call_id,
                ok=False,
                error_code="internal_error",
                error_message=msg,
                duration_ms=duration_ms,
                path=self._audit_path,
            )
            meta = _meta(registration, call_id, run_id, duration_ms,
                         extra=registration.extra_meta)
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
            path=self._audit_path,
        )

        meta = _meta(registration, call_id, run_id, duration_ms,
                     extra=registration.extra_meta)
        if isinstance(result, ToolResponse):
            # Refresh the meta fields on whatever the tool returned.
            # We intentionally don't replace ``data``; the tool may have
            # populated it for an ``ok=True`` partial-success variant.
            result.meta = meta
            return result.model_dump()

        return ToolResponse.ok_response(data=result, meta=meta).model_dump()


# --- Wrapper the FastMCP decorator uses ---------------------------------


def _make_wrapper(
    service: ToolExecutionService,
    reg: _Registration,
) -> Callable[..., dict[str, Any]]:
    """Build the callable that FastMCP actually invokes.

    The wrapper has an **explicit** signature, synthesised from the
    registration's :attr:`_Registration.param_names`. Each named
    parameter is typed ``Any`` with a default of ``None`` so:

    1. FastMCP's Pydantic-based schema generator emits a proper
       schema with one field per declared parameter.
    2. Agents see real top-level input fields, not a single
       ``kwargs`` blob.
    3. The wrapper collects the (possibly partial) kwargs into a
       dict and forwards to the chokepoint.

    We avoid ``**kwargs`` because FastMCP translates it into a single
    required ``kwargs`` dict, which means agents have to wrap every
    call in ``{"kwargs": {...}}`` — not what the spec calls for.
    """
    return _synthesise_wrapper(service, reg)


def _synthesise_wrapper(
    service: ToolExecutionService,
    reg: _Registration,
) -> Callable[..., dict[str, Any]]:
    """Build a wrapper function whose signature matches ``reg.param_names``.

    Implementation: write a small Python source string and exec it
    into the module's namespace. This is the cleanest way to give a
    callable an arbitrary signature at runtime without writing a
    metaclass.
    """
    params = list(reg.param_names)
    fn_name = f"_lmcp_tool_{reg.tool.replace('.', '_')}"
    if params:
        # ``value: Any = None`` per parameter; the agent can omit any
        # field and the tool body decides whether absence is an error.
        sig = ", ".join(f"{p}: Any = None" for p in params)
    else:
        sig = ""
    body_lines = [
        f"def {fn_name}({sig}) -> dict[str, Any]:",
        f"    return _wrapper_invoke(_service, {reg.tool!r}, dict(",
    ]
    for p in params:
        body_lines.append(f"        {p}={p},")
    body_lines.append("    ))")
    src = "\n".join(body_lines) + "\n"
    ns: dict[str, Any] = {
        "Any": Any,
        "dict": dict,
        "_wrapper_invoke": _wrapper_invoke,
        "_service": service,
    }
    exec(compile(src, f"<lmcp-tool {reg.tool}>", "exec"), ns)
    fn: Callable[..., dict[str, Any]] = ns[fn_name]
    fn.__doc__ = reg.description or f"Tool: {reg.tool}"
    return fn


def _wrapper_invoke(service: ToolExecutionService, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Helper invoked by synthesised wrappers; routes through the chokepoint."""
    reg = service.get_registration(tool_name)
    return service.invoke(reg, args)


# --- Helpers -------------------------------------------------------------


def _meta(
    registration: _Registration,
    audit_id: str,
    run_id: str,
    duration_ms: int,
    *,
    extra: dict[str, Any] | None = None,
) -> ToolMeta:
    """Build a :class:`ToolMeta` for a successful call."""
    base_extra = dict(registration.extra_meta)
    if extra:
        base_extra.update(extra)
    meta = ToolMeta(
        tool=registration.tool,
        duration_ms=duration_ms,
        audit_id=audit_id,
        run_id=run_id,
    )
    # ``next_actions`` and ``output_handle`` are first-class fields on
    # ToolMeta. Anything else from extra_meta lands in a side-channel
    # that the server attaches below — but for now we only forward
    # the documented fields to keep the envelope pinned to the schema.
    next_actions = base_extra.pop("next_actions", None)
    if next_actions is not None:
        meta.next_actions = list(next_actions)
    output_handle = base_extra.pop("output_handle", None)
    if output_handle is not None:
        meta.output_handle = output_handle
    log_path = base_extra.pop("log_path", None)
    if log_path is not None:
        meta.log_path = log_path
    return meta


def _safe_message(exc: BaseException) -> str:
    """One-line, redacted-safe message for an exception.

    No traceback. Length-capped so a hostile agent can't blow up
    context by stuffing a megabyte of arg into a tool call.
    """
    name = type(exc).__name__
    text = str(exc).strip()
    if not text:
        return name
    truncated = text[:_MAX_ERROR_MESSAGE_LEN]
    # Apply the redactor so a value raised from an unredacted source
    # doesn't leak a token through the error envelope.
    redacted, _ = redact(f"{name}: {truncated}")
    return redacted


# Re-export the carrier so the public helpers in :mod:`tools._common`
# can construct it without crossing module boundaries.
__all__ = [
    "DEFAULT_POLICY_VERSION",
    "DEFAULT_PROFILE",
    "ToolContext",
    "ToolExecutionService",
    "ToolLogic",
    "_ToolErrorResponse",
]
