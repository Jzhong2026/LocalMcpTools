"""Profile capability decision matrix."""

from __future__ import annotations

import pytest

from localmcptools.policy.authorize import Decision, check
from localmcptools.policy.profile import Profile


@pytest.mark.parametrize("profile", list(Profile))
def test_read_only_tools_are_available_to_every_profile(profile: Profile) -> None:
    assert check(profile, "workspace.inspect") is Decision.ALLOW


def test_workspace_exec_side_effects_need_approval_only_for_right_profile() -> None:
    assert check(Profile.OBSERVE, "shell.run_command") is Decision.DENY
    assert check(Profile.WORKSPACE_EXEC, "shell.run_command") is Decision.NEED_APPROVAL


def test_other_profile_bound_capabilities_fail_closed() -> None:
    assert check(Profile.WORKSPACE_EXEC, "process.start_dev_server") is Decision.DENY
    assert check(Profile.INTERACTIVE_UI, "ui.click_element") is Decision.NEED_APPROVAL
    assert check(Profile.OBSERVE, "unregistered.tool") is Decision.DENY
