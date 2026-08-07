"""``workspace.*`` tools.

Three tools live here:

- :func:`workspace_register` — registers an absolute directory and
  returns ``{workspace_id, canonical_root, profile}``. Used by the
  agent the first time it interacts with a project. Idempotent.

- :func:`workspace_inspect` — given a ``workspace_id``, returns the
  full REQ-WS-2 payload (project type, Git, presets, runtimes).

- :func:`workspace_search_text` — workspace-scoped regex/glob grep.
  Matches outside ``canonical_root`` are silently excluded. Binary
  files are skipped, never crashed on.

A read-only :func:`workspace_list` exposes the registry for the UI
and for agents that want to enumerate.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, cast

from ..workspaces import inspect as ws_inspect
from ..workspaces.registry import (
    InvalidPath,
    WorkspaceNotRegistered,
    assert_inside_workspace,
    list_workspaces,
    register,
    resolve,
)
from ._errors import fail

# Cap on how many bytes we'll read from a single file while grepping.
# Bigger than this and we skip the file with a synthetic skipped entry.
_GREP_FILE_BYTE_CAP = 2 * 1024 * 1024  # 2 MiB

# Same threshold as the binary detector in :func:`tools.fs`.
_BINARY_SNIFF_BYTES = 8 * 1024


# --- Register / list ------------------------------------------------------


def workspace_register(args: dict[str, Any]) -> Any:
    """Register a directory. Returns ``{workspace_id, canonical_root, profile}``."""
    path_raw = args.get("path")
    if not isinstance(path_raw, str) or not path_raw:
        fail(
            code="invalid_args",
            message="`path` must be a non-empty absolute directory string",
            tool="workspace.register",
            audit_id="pending",
            run_id="pending",
            suggestion="pass `path` as an absolute Windows path, e.g. "
                       "`D:\\\\AI\\\\Projects\\\\MyApp`",
        )
    path = cast("str", path_raw)
    try:
        ws = register(path, profile="observe", notes=args.get("notes"))
    except InvalidPath as exc:
        fail(
            code="invalid_path",
            message=str(exc),
            tool="workspace.register",
            audit_id="pending",
            run_id="pending",
            suggestion="provide an absolute directory inside the user's "
                       "home / projects root; `..` segments are rejected",
        )
    return {
        "workspace_id": ws.id,
        "canonical_root": ws.canonical_root,
        "profile": ws.profile,
    }


def workspace_list(args: dict[str, Any]) -> Any:
    """List every registered workspace."""
    _ = args
    return {
        "workspaces": [
            {
                "workspace_id": w.id,
                "canonical_root": w.canonical_root,
                "profile": w.profile,
                "registered_at": w.registered_at,
                "notes": w.notes,
            }
            for w in list_workspaces()
        ]
    }


# --- Resolve helper -------------------------------------------------------


def _resolve_workspace(workspace_id: Any, tool: str) -> Any:
    """Resolve ``workspace_id`` to a :class:`Workspace`, or fail."""
    if not workspace_id or not isinstance(workspace_id, str):
        fail(
            code="invalid_args",
            message="`workspace_id` is required",
            tool=tool,
            audit_id="pending",
            run_id="pending",
            suggestion="call workspace.register first to obtain a workspace_id",
        )
    try:
        return resolve(workspace_id)
    except WorkspaceNotRegistered as exc:
        fail(
            code="workspace_not_registered",
            message=str(exc),
            tool=tool,
            audit_id="pending",
            run_id="pending",
            suggestion="list_workspaces then re-call with a valid id",
        )


# --- workspace.inspect ----------------------------------------------------


def workspace_inspect(args: dict[str, Any]) -> Any:
    ws = _resolve_workspace(args.get("workspace_id"), "workspace.inspect")
    root = Path(ws.canonical_root)
    if not root.is_dir():
        fail(
            code="invalid_path",
            message=f"workspace directory no longer exists: {ws.canonical_root!r}",
            tool="workspace.inspect",
            audit_id="pending",
            run_id="pending",
            suggestion="re-register the directory",
            workspace_id=ws.id,
        )
    payload = ws_inspect.inspect_workspace(root)
    payload["workspace_id"] = ws.id
    payload["canonical_root"] = ws.canonical_root
    return payload


# --- workspace.search_text -----------------------------------------------


def workspace_search_text(args: dict[str, Any]) -> Any:
    ws = _resolve_workspace(args.get("workspace_id"), "workspace.search_text")
    pattern_raw = args.get("pattern")
    if not isinstance(pattern_raw, str) or not pattern_raw:
        fail(
            code="invalid_args",
            message="`pattern` must be a non-empty string",
            tool="workspace.search_text",
            audit_id="pending",
            run_id="pending",
            suggestion="pass a regex (Python re syntax), e.g. 'TODO|FIXME'",
            workspace_id=ws.id,
        )
    pattern = cast("str", pattern_raw)
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        fail(
            code="invalid_args",
            message=f"`pattern` is not a valid regex: {exc}",
            tool="workspace.search_text",
            audit_id="pending",
            run_id="pending",
            suggestion="fix the regex; Python re syntax only",
            workspace_id=ws.id,
        )
    max_results = int(args.get("max_results") or 50)
    if max_results < 1:
        max_results = 50

    include_glob = args.get("include_glob")
    root = Path(ws.canonical_root)
    matches: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    truncated = False

    for dirpath, dirnames, filenames in os.walk(root):
        # Skip noisy / huge directories by default. The agent can still
        # search them by mounting a separate workspace.
        dirnames[:] = [
            d for d in dirnames
            if d not in {"node_modules", ".git", "dist", "bin", "obj", ".venv",
                         "__pycache__", ".mypy_cache", ".pytest_cache",
                         ".angular", "ui_assets"}
        ]
        for filename in filenames:
            if include_glob and not _glob_match(filename, include_glob):
                continue
            fpath = Path(dirpath) / filename
            try:
                assert_inside_workspace(ws, fpath)
            except Exception:
                continue
            try:
                size = fpath.stat().st_size
            except OSError:
                continue
            if size > _GREP_FILE_BYTE_CAP:
                skipped.append({"file": str(fpath), "reason": "too_large"})
                continue
            try:
                with fpath.open("rb") as fh:
                    raw = fh.read(_BINARY_SNIFF_BYTES)
                if b"\x00" in raw:
                    skipped.append({"file": str(fpath), "reason": "binary"})
                    continue
                text = raw.decode("utf-8", errors="replace")
                if size > _BINARY_SNIFF_BYTES:
                    with fpath.open("r", encoding="utf-8", errors="replace") as fh:
                        fh.seek(_BINARY_SNIFF_BYTES)
                        text += fh.read()
            except OSError:
                skipped.append({"file": str(fpath), "reason": "unreadable"})
                continue

            for line_no, line in enumerate(text.splitlines()):
                if rx.search(line):
                    rel = str(fpath.relative_to(root))
                    matches.append({"file": rel, "line": line_no, "text": line})
                    if len(matches) >= max_results:
                        truncated = True
                        break
            if truncated:
                break
        if truncated:
            break

    next_actions: list[str] = []
    if truncated:
        next_actions.append("narrow pattern to workspace (max_results hit)")
    if any(s["reason"] == "too_large" for s in skipped):
        next_actions.append("some files were skipped because they exceed 2 MiB")
    if ".." in pattern:
        next_actions.append("narrow pattern to workspace")

    return {
        "matches": matches,
        "skipped": skipped,
        "truncated": truncated,
        "next_actions": next_actions,
    }


# --- helpers --------------------------------------------------------------


def _glob_match(name: str, pattern: str) -> bool:
    """Cheap glob match for a single filename (no ``**``)."""
    rx = re.escape(pattern).replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
    return re.fullmatch(rx, name) is not None


__all__ = [
    "workspace_register",
    "workspace_list",
    "workspace_inspect",
    "workspace_search_text",
]
