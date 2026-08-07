"""Capability decision table, intentionally independent of MCP transport."""

from __future__ import annotations

from enum import StrEnum

from .profile import Profile


class Decision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    NEED_APPROVAL = "need_approval"


_READ_ONLY_PREFIXES = ("environment.", "output.", "fs.")
_OBSERVE_WORKSPACE_TOOLS = {
    "workspace.register", "workspace.list", "workspace.inspect", "workspace.search_text", "workspace.git_status",
    "process.get_status", "process.list_managed", "process.list_listening_ports", "process.find_by_port",
}
_WORKSPACE_EXEC_TOOLS = {"shell.run_command", "workspace.run_test", "workspace.build", "workspace.lint"}
_MANAGED_PROCESS_TOOLS = {"process.start_dev_server", "process.stop_managed"}
_INTERACTIVE_UI_TOOLS = {"ui.click_element", "ui.type_text", "ui.act_and_verify"}


def check(profile: Profile, capability: str) -> Decision:
    """Decide whether ``profile`` may use a named tool capability."""
    tool = capability.split(":", 1)[-1]
    if tool.startswith(_READ_ONLY_PREFIXES) or tool in _OBSERVE_WORKSPACE_TOOLS:
        return Decision.ALLOW
    if tool in _WORKSPACE_EXEC_TOOLS:
        return Decision.NEED_APPROVAL if profile is Profile.WORKSPACE_EXEC else Decision.DENY
    if tool in _MANAGED_PROCESS_TOOLS:
        return Decision.NEED_APPROVAL if profile is Profile.MANAGED_PROCESS else Decision.DENY
    if tool in _INTERACTIVE_UI_TOOLS:
        return Decision.NEED_APPROVAL if profile is Profile.INTERACTIVE_UI else Decision.DENY
    return Decision.DENY


__all__ = ["Decision", "check"]
