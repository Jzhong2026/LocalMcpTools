"""MCP tool bodies for managed development servers and TCP listeners."""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any, cast

from ..execution import background
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
from ..process import manager, ports
from ..process.presets import PRESETS, UnknownPreset
from ..process.presets import resolve as resolve_preset
from ..safety.rules import RuleEngine, record_hit
from ..workspaces.registry import WorkspaceNotRegistered
from ..workspaces.registry import resolve as resolve_workspace
from ._common import ToolMeta, ToolResponse
from ._errors import fail


def process_start_dev_server(args: dict[str, Any]) -> ToolResponse:
    tool = "process.start_dev_server"
    workspace_id = args.get("workspace_id")
    preset_name = args.get("preset")
    if not isinstance(workspace_id, str) or not isinstance(preset_name, str):
        fail(code="invalid_args", message="workspace_id and preset are required", tool=tool, audit_id="pending", run_id="pending")
    workspace_id = cast(str, workspace_id)
    preset_name = cast(str, preset_name)
    try:
        workspace = resolve_workspace(workspace_id)
        profile = current(workspace.id)
    except WorkspaceNotRegistered as exc:
        fail(code="workspace_not_registered", message=str(exc), tool=tool, audit_id="pending", run_id="pending")
    if check(profile, f"{profile.value}:{tool}") is Decision.DENY:
        fail(code="insufficient_capability", message="workspace profile cannot start managed processes", tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace.id, suggestion="an operator must select the managed_process profile")
    try:
        preset, argv = resolve_preset(
            preset_name, args.get("args"), workspace_id=workspace.id
        )
    except UnknownPreset as exc:
        fail(code="unknown_preset", message=str(exc), tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace.id, data={"supported_presets": sorted(PRESETS)})
    except ValueError as exc:
        fail(code="invalid_args", message=str(exc), tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace.id)
    command = subprocess.list2cmdline(list(argv))
    engine = RuleEngine()
    engine.reload()
    hit = engine.match(command)
    if hit is not None:
        record_hit(hit.rule_id, command)
        fail(code="denied_by_rule", message="resolved command matched a deny rule", tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace.id, blocked_by=hit.rule_id, severity=hit.severity, suggestion=hit.suggestion)
    capability = f"{profile.value}:{tool}"
    approval_id = args.get("approval_id")
    preview = {"preset": preset.name, "command_resolved": command, "cwd": workspace.canonical_root}
    if not isinstance(approval_id, str) or not approval_id:
        approval = request(workspace.id, capability, args, profile=profile.value)
        fail(code="approval_required", message="human approval is required before starting this process", tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace.id, approval_id=approval.id, data=preview, next_actions=["review the resolved command, approve, and retry"])
    approval_id = cast(str, approval_id)
    try:
        consume(approval_id, digest_for(tool, args, workspace.id, profile.value))
    except ApprovalDigestMismatch as exc:
        fail(code="approval_digest_mismatch", message=str(exc), tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace.id)
    except ApprovalExpired as exc:
        fail(code="approval_expired", message=str(exc), tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace.id)
    except ApprovalNotApproved:
        fail(code="approval_required", message="approval is still pending", tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace.id, approval_id=approval_id)
    try:
        item = background.start_dev_server(
            workspace_id=workspace.id, cwd=workspace.canonical_root,
            preset=preset, argv=argv, env=os.environ,
        )
    except (OSError, RuntimeError) as exc:
        fail(code="internal_error", message=f"managed process could not start: {exc}", tool=tool, audit_id="pending", run_id="pending", workspace_id=workspace.id)
    meta = ToolMeta(tool=tool, duration_ms=0, audit_id="pending", run_id="pending", workspace_id=workspace.id, output_handle=item.log_handle)
    return ToolResponse.ok_response(data={
        "id": item.id, "pid": item.pid, "command_resolved": item.command,
        "log_handle": item.log_handle, "port": item.port,
    }, meta=meta)


def process_get_status(args: dict[str, Any]) -> ToolResponse:
    tool = "process.get_status"
    item = _get_item(args, tool)
    now = int(time.time() * 1000)
    end = item.finished_at or now
    data = {
        "id": item.id, "status": item.status, "pid": item.pid,
        "started_at": item.started_at, "duration_ms": max(0, end - item.started_at),
        "exit_code": item.exit_code if item.status == "exited" else None,
        "tail": artifacts.tail(item.log_handle, 20), "log_handle": item.log_handle,
        "port": item.port,
    }
    meta = ToolMeta(tool=tool, duration_ms=0, audit_id="pending", run_id="pending", workspace_id=item.workspace_id, output_handle=item.log_handle)
    return ToolResponse.ok_response(data=data, meta=meta)


def process_list_managed(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = args.get("workspace_id")
    if workspace_id is not None and not isinstance(workspace_id, str):
        fail(code="invalid_args", message="workspace_id must be a string", tool="process.list_managed", audit_id="pending", run_id="pending")
    return {"processes": [item.as_dict() for item in manager.list_managed(workspace_id)]}


def process_stop_managed(args: dict[str, Any]) -> ToolResponse:
    tool = "process.stop_managed"
    item = _get_item(args, tool)
    try:
        profile = current(item.workspace_id)
    except WorkspaceNotRegistered as exc:
        fail(code="workspace_not_registered", message=str(exc), tool=tool, audit_id="pending", run_id="pending")
    if check(profile, f"{profile.value}:{tool}") is Decision.DENY:
        fail(code="insufficient_capability", message="workspace profile cannot stop managed processes", tool=tool, audit_id="pending", run_id="pending", workspace_id=item.workspace_id)
    approval_id = args.get("approval_id")
    capability = f"{profile.value}:{tool}"
    if not isinstance(approval_id, str) or not approval_id:
        approval = request(item.workspace_id, capability, args, profile=profile.value)
        fail(code="approval_required", message="human approval is required before stopping this process", tool=tool, audit_id="pending", run_id="pending", workspace_id=item.workspace_id, approval_id=approval.id, data={"id": item.id, "pid": item.pid, "command_resolved": item.command})
    approval_id = cast(str, approval_id)
    try:
        consume(approval_id, digest_for(tool, args, item.workspace_id, profile.value))
    except ApprovalDigestMismatch as exc:
        fail(code="approval_digest_mismatch", message=str(exc), tool=tool, audit_id="pending", run_id="pending", workspace_id=item.workspace_id)
    except ApprovalExpired as exc:
        fail(code="approval_expired", message=str(exc), tool=tool, audit_id="pending", run_id="pending", workspace_id=item.workspace_id)
    except ApprovalNotApproved:
        fail(code="approval_required", message="approval is still pending", tool=tool, audit_id="pending", run_id="pending", workspace_id=item.workspace_id, approval_id=approval_id)
    stopped = background.stop(item.id, graceful=args.get("graceful") is not False)
    meta = ToolMeta(tool=tool, duration_ms=0, audit_id="pending", run_id="pending", workspace_id=item.workspace_id, output_handle=item.log_handle)
    return ToolResponse.ok_response(data=stopped.as_dict(), meta=meta)


def process_list_listening_ports(_args: dict[str, Any]) -> dict[str, Any]:
    return {"ports": [item.as_dict() for item in ports.list_listening_ports()]}


def process_find_by_port(args: dict[str, Any]) -> dict[str, Any]:
    value = args.get("port")
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        fail(code="invalid_args", message="port must be an integer between 1 and 65535", tool="process.find_by_port", audit_id="pending", run_id="pending")
    value = cast(int, value)
    item = ports.find_by_port(value)
    if item is None:
        fail(code="port_not_found", message=f"nothing is listening on TCP port {value}", tool="process.find_by_port", audit_id="pending", run_id="pending")
    assert item is not None
    return item.as_dict()


def process_kill_not_exposed(_args: dict[str, Any]) -> None:
    """Fail closed if an adapter manually wires the forbidden kill surface."""
    fail(
        code="not_exposed", message="arbitrary PID termination is not exposed",
        tool="process.kill", audit_id="pending", run_id="pending",
        suggestion="stop only a process returned by process.list_managed",
    )


def _get_item(args: dict[str, Any], tool: str) -> manager.ManagedProcess:
    process_id = args.get("id")
    if not isinstance(process_id, str) or not process_id:
        fail(code="invalid_args", message="id is required", tool=tool, audit_id="pending", run_id="pending")
    process_id = cast(str, process_id)
    try:
        return manager.find_by_id(process_id)
    except manager.ManagedProcessNotFound as exc:
        fail(code="managed_process_not_found", message=str(exc), tool=tool, audit_id="pending", run_id="pending")
    raise AssertionError("unreachable")


__all__ = ["process_find_by_port", "process_get_status", "process_list_listening_ports", "process_list_managed", "process_start_dev_server", "process_stop_managed"]
