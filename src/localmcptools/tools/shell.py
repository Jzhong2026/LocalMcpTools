"""The explicit, approval-gated shell escape hatch."""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any, cast

from ..config.settings import load_settings
from ..execution.concurrency import QueueTimeout
from ..execution.context import current_gate
from ..execution.powershell import build_powershell_args
from ..execution.runner import run
from ..persistence import artifacts
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
from ..safety.redact import redact
from ..safety.rules import RuleEngine, record_hit
from ..workspaces.registry import WorkspaceNotRegistered, resolve
from ._common import ToolMeta, ToolResponse
from ._errors import fail


def shell_run_command(args: dict[str, Any]) -> ToolResponse:
    """Run a PowerShell command only within an approved workspace."""
    workspace_id = args.get("workspace_id")
    command = args.get("cmd")
    if not isinstance(workspace_id, str) or not isinstance(command, str) or not command.strip():
        fail(code="invalid_args", message="workspace_id and non-empty cmd are required", tool="shell.run_command", audit_id="pending", run_id="pending")
    workspace_id = cast(str, workspace_id)
    command = cast(str, command)
    try:
        workspace = resolve(workspace_id)
        profile = current(workspace.id)
    except WorkspaceNotRegistered as exc:
        fail(code="workspace_not_registered", message=str(exc), tool="shell.run_command", audit_id="pending", run_id="pending")
    capability = f"{profile.value}:shell.run_command"
    if check(profile, capability) is Decision.DENY:
        fail(code="insufficient_capability", message="workspace profile cannot run controlled shell commands", tool="shell.run_command", audit_id="pending", run_id="pending", workspace_id=workspace.id, suggestion="an operator must grant workspace_exec through the approval authority")

    # Rules run before consuming the approval so even a valid approval cannot
    # be spent on an operation the safety layer rejects.
    engine = RuleEngine()
    engine.reload()
    hit = engine.match(command)
    if hit is not None:
        record_hit(hit.rule_id, command)
        fail(code="denied_by_rule", message="command matched a deny rule", tool="shell.run_command", audit_id="pending", run_id="pending", workspace_id=workspace.id, blocked_by=hit.rule_id, severity=hit.severity, suggestion=hit.suggestion)

    approval_id = args.get("approval_id")
    digest = digest_for("shell.run_command", args, workspace.id, profile.value)
    preview = {"command_resolved": command, "cwd": workspace.canonical_root}
    if not isinstance(approval_id, str) or not approval_id:
        item = request(workspace.id, capability, args, profile=profile.value)
        fail(code="approval_required", message="human approval is required before running this command", tool="shell.run_command", audit_id="pending", run_id="pending", workspace_id=workspace.id, approval_id=item.id, data=preview, next_actions=["review the resolved command and working directory, then approve and retry"])
    approval_id = cast(str, approval_id)
    try:
        consume(approval_id, digest)
    except ApprovalDigestMismatch as exc:
        fail(code="approval_digest_mismatch", message=str(exc), tool="shell.run_command", audit_id="pending", run_id="pending", workspace_id=workspace.id)
    except ApprovalExpired as exc:
        fail(code="approval_expired", message=str(exc), tool="shell.run_command", audit_id="pending", run_id="pending", workspace_id=workspace.id)
    except ApprovalNotApproved:
        fail(code="approval_required", message="approval is still pending", tool="shell.run_command", audit_id="pending", run_id="pending", workspace_id=workspace.id, approval_id=approval_id)

    try:
        timeout_ms = _timeout_ms(args.get("timeout_ms"))
        env, ignored_env = _filtered_env(workspace.id, args.get("env"))
    except ValueError as exc:
        fail(code="invalid_args", message=str(exc), tool="shell.run_command", audit_id="pending", run_id="pending", workspace_id=workspace.id)
    powershell = shutil.which("powershell") or shutil.which("pwsh") or "powershell.exe"
    try:
        result = asyncio.run(run([powershell, *build_powershell_args(command)], cwd=workspace.canonical_root, env=env, timeout_ms=timeout_ms, gate=current_gate()))
    except QueueTimeout:
        fail(code="queue_timeout", message="execution queue timeout", tool="shell.run_command", audit_id="pending", run_id="pending", workspace_id=workspace.id)
    redacted_output, _ = redact(result.output)
    handle = artifacts.write(redacted_output)
    meta = ToolMeta(tool="shell.run_command", duration_ms=0, audit_id="pending", run_id="pending", workspace_id=workspace.id, output_handle=handle)
    if result.timed_out:
        return ToolResponse.error_response(code="timed_out", message="command exceeded timeout", meta=meta, suggestion="inspect output artifact before retrying")
    return ToolResponse.ok_response(data={**preview, "exit_code": result.exit_code, "stdout_bytes": result.stdout_bytes, "stderr_bytes": result.stderr_bytes, "ignored_env_keys": ignored_env, "cwd_forced": "cwd" in args}, meta=meta)


def _timeout_ms(value: Any) -> int:
    if value is None:
        return 120_000
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 3_600_000:
        raise ValueError("timeout_ms must be an integer between 1 and 3600000")
    return cast(int, value)


def _filtered_env(workspace_id: str, raw: Any) -> tuple[dict[str, str], list[str]]:
    if raw is None:
        return dict(os.environ), []
    if not isinstance(raw, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in raw.items()):
        raise ValueError("env must be an object of string keys and string values")
    settings = load_settings()
    allowlists = settings.get("workspaces", {}).get("env_allowlists", {})
    allowed = set(allowlists.get(workspace_id, [])) if isinstance(allowlists, dict) else set()
    accepted = {key: value for key, value in raw.items() if key in allowed}
    return {**os.environ, **accepted}, sorted(set(raw) - set(accepted))


__all__ = ["shell_run_command"]
