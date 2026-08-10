"""Minimal-valid-arguments registry.

For every MCP tool exposed by ``localmcptools``, this module declares
the **smallest argument set** that lets the tool reach its main code
path without crashing on a missing required field. Some tools need
no arguments; others need a workspace, an artifact handle, a window
id, etc.

The goal of ``test_01_tool_surface_stdio.py`` is to prove every tool
goes through the chokepoint — not to prove every tool succeeds in
isolation. So ``MIN_ARGS`` is allowed to be a "minimum input" that
causes the tool to either:

* return ``ok=true`` (preferred — proves the happy path), or
* return ``ok=false`` with a **stable error_code** like
  ``workspace_not_found``, ``artifact_not_found``,
  ``window_not_authorized`` etc. (still proves the tool ran and was
  audited).

The expected error codes are listed in :data:`EXPECTED_ERROR_CODES`.
If a tool returns a different error_code or no error_code at all,
the test fails — that's a regression worth catching.
"""

from __future__ import annotations

from pathlib import Path

# --- Minimal args ---------------------------------------------------------
#
# Each value is the smallest dict that the tool will accept without
# raising an "invalid_args" error. The actual outcome may be ok=true
# or a controlled ok=false (see EXPECTED_ERROR_CODES below).

MIN_ARGS: dict[str, dict] = {
    # --- observe + workspace -------------------------------------------------
    "environment.get": {},
    "workspace.list": {},
    "workspace.register": {"path": str(Path.cwd())},  # absolute path of repo root
    "workspace.inspect": {"workspace_id": "nope"},
    "workspace.search_text": {"workspace_id": "nope", "pattern": "."},
    "workspace.git_status": {"workspace_id": "nope"},
    "workspace.build": {"workspace_id": "nope"},
    "workspace.lint": {"workspace_id": "nope"},
    "workspace.run_test": {"workspace_id": "nope"},
    # --- fs / output ---------------------------------------------------------
    "fs.read_range": {"workspace_id": "nope", "path": "x"},
    "fs.tail_log_file": {"workspace_id": "nope", "path": "x"},
    "fs.grep_files": {"workspace_id": "nope", "pattern": "."},
    "output.tail": {"handle": "art://nope"},
    "output.read_range": {"handle": "art://nope"},
    "output.search": {"handle": "art://nope", "pattern": "."},
    # --- diagnostics ---------------------------------------------------------
    "diagnostics.collect": {},
    "diagnostics.explain_failure": {"run_id": "nope"},
    # --- runtime -------------------------------------------------------------
    "runtime.detect_runtime": {},
    "runtime.get_env": {"name": "PATH"},
    "runtime.list_path": {},
    # --- process -------------------------------------------------------------
    "process.list_listening_ports": {},
    "process.list_managed": {},
    "process.find_by_port": {"port": 65534},  # unlikely to be bound; not "0" (invalid range)
    "process.get_status": {"id": "nope"},
    "process.start_dev_server": {
        "workspace_id": "nope",
        "preset": "python-http",
    },
    "process.stop_managed": {"id": "nope"},
    # --- vscode --------------------------------------------------------------
    "vscode.get_problems": {},
    "vscode.get_installed_extensions": {},
    "vscode.get_debug_sessions": {},
    "vscode.get_logs": {},
    # --- shell (gated by policy) --------------------------------------------
    "shell.run_command": {"workspace_id": "nope", "cmd": "echo hi"},
    # --- ui / ocr (need an authorized window or artifact) -------------------
    "ui.list_windows": {},
    "ui.authorize_window": {"hwnd": 0},
    "ui.revoke_window": {"window_id": "nope"},
    "ui.get_ui_tree": {"window_id": "nope"},
    "ui.find_element": {"window_id": "nope"},
    "ui.click_element": {"window_id": "nope"},
    "ui.type_text": {"window_id": "nope", "text": "x"},
    "ui.screenshot_full": {},
    "ui.screenshot_window": {"window_id": "nope"},
    "ui.screenshot_region": {"region": [0, 0, 1, 1]},
    "ui.act_and_verify": {"action_type": "click"},
    "ocr.ocr_region": {"window_id": "nope"},
    "ocr.find_text": {"query": "x"},
    "ocr.assert_text": {"expected": "x"},
}


# --- Expected error codes -------------------------------------------------
#
# When a tool returns ok=false, we expect one of these codes. If a tool
# starts returning a new error_code, that's a regression we want to
# hear about.
#
# "*" means "any error_code is acceptable" — used for tools whose error
# vocabulary is broad (e.g. shell.run_command can hit policy rules).
EXPECTED_ERROR_CODES: dict[str, set[str] | str] = {
    # Tools that should succeed without any preconditions
    "environment.get": set(),
    "workspace.list": set(),
    "diagnostics.collect": set(),
    "diagnostics.explain_failure": {"run_not_found"},
    "runtime.detect_runtime": set(),
    "runtime.get_env": set(),
    "runtime.list_path": set(),
    "process.list_listening_ports": set(),
    "process.list_managed": set(),
    "process.find_by_port": {"port_not_found"},  # 65534 is unlikely to be bound
    "process.get_status": {"managed_process_not_found"},
    "process.start_dev_server": {"workspace_not_registered", "preset_not_found", "unknown_preset"},
    "process.stop_managed": {"managed_process_not_found"},
    "vscode.get_problems": set(),
    "vscode.get_installed_extensions": set(),
    "vscode.get_debug_sessions": set(),
    "vscode.get_logs": set(),
    "ui.list_windows": set(),
    "ui.screenshot_full": set(),
    "ui.screenshot_region": set(),
    "ocr.find_text": set(),
    "ocr.assert_text": set(),
    # Workspace tools that should hit a clean "not found" since
    # we passed workspace_id="nope"
    "workspace.register": {"invalid_path"},  # path must be absolute; we use cwd() which IS absolute
    "workspace.inspect": {"workspace_not_registered", "invalid_args"},  # accept either from real run
    "workspace.search_text": {"workspace_not_registered"},
    "workspace.git_status": {"workspace_not_registered"},
    "workspace.build": {"workspace_not_registered"},
    "workspace.lint": {"workspace_not_registered"},
    "workspace.run_test": {"workspace_not_registered"},
    # fs / output need real paths / handles
    "fs.read_range": {"workspace_not_registered", "invalid_path"},
    "fs.tail_log_file": {"workspace_not_registered", "invalid_path"},
    "fs.grep_files": {"workspace_not_registered", "invalid_regex", "invalid_path"},
    "output.tail": {"artifact_not_found"},
    "output.read_range": {"artifact_not_found"},
    "output.search": {"artifact_not_found"},
    # shell is broad — accept any policy/regex/error_code
    "shell.run_command": "*",
    # UI / OCR tools need a real authorized window; accept any
    # "not_authorized" / "window_not_found" / similar
    "ui.authorize_window": "*",
    "ui.revoke_window": "*",
    "ui.get_ui_tree": "*",
    "ui.find_element": "*",
    "ui.click_element": "*",
    "ui.type_text": "*",
    "ui.screenshot_window": "*",
    "ui.act_and_verify": "*",
    "ocr.ocr_region": "*",
}