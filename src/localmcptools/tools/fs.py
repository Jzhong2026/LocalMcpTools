"""``fs.*`` tools — workspace-scoped read helpers.

Three tools:

- :func:`fs_read_range` — read ``lines[start:end]`` from a file.
- :func:`fs_tail_log_file` — last ``n`` lines, never loads >5 MiB into
  memory.
- :func:`fs_grep_files` — workspace-scoped regex grep with
  ``include_glob`` support and default exclude directories.

All three enforce the workspace boundary via
:func:`assert_inside_workspace` so an escape attempt fails uniformly
across the tool surface. Binary files (null byte in first 8 KiB)
return ``binary_file``; the file is never crashed on.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from ..workspaces.registry import (
    InvalidPath,
    WorkspaceNotRegistered,
    assert_inside_workspace,
    resolve,
)
from ._errors import fail

_BINARY_SNIFF_BYTES = 8 * 1024
_MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MiB; tail won't load more than this

# Default-excluded directories (same set as workspace.search_text).
_EXCLUDE_DIRS = frozenset({
    "node_modules", ".git", "dist", "bin", "obj", ".venv",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".angular", "ui_assets",
})


def _resolve_ws(workspace_id: Any, tool: str) -> Any:
    if not workspace_id or not isinstance(workspace_id, str):
        fail(
            code="invalid_args",
            message="`workspace_id` is required",
            tool=tool,
            audit_id="pending",
            run_id="pending",
            suggestion="call workspace.register first",
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


def _resolve_file(workspace_id: Any, path: Any, tool: str) -> tuple[Any, Path]:
    ws = _resolve_ws(workspace_id, tool)
    if not isinstance(path, str) or not path:
        fail(
            code="invalid_args",
            message="`path` is required",
            tool=tool,
            audit_id="pending",
            run_id="pending",
            suggestion="pass a path relative to the workspace root",
            workspace_id=ws.id,
        )
    # The agent provides a path; we require it to live under the
    # workspace. ``assert_inside_workspace`` handles both path-escape
    # and absolute-but-outside cases.
    abs_path_str: str
    try:
        # Allow either absolute paths (resolved against the workspace
        # boundary) or relative paths (joined to the workspace root).
        candidate = path if os.path.isabs(path) else str(Path(ws.canonical_root) / path)
        abs_path_str = assert_inside_workspace(ws, candidate)
    except InvalidPath as exc:
        fail(
            code="invalid_path",
            message=str(exc),
            tool=tool,
            audit_id="pending",
            run_id="pending",
            suggestion="pass a path inside the registered workspace",
            workspace_id=ws.id,
        )
    p = Path(abs_path_str)
    if not p.exists() or not p.is_file():
        fail(
            code="invalid_path",
            message=f"file does not exist: {abs_path_str!r}",
            tool=tool,
            audit_id="pending",
            run_id="pending",
            suggestion="check workspace.search_text for valid paths",
            workspace_id=ws.id,
        )
    return ws, p


def _is_binary(path: Path) -> bool:
    """True if the first 8 KiB of the file contains a NUL byte."""
    try:
        with path.open("rb") as fh:
            chunk = fh.read(_BINARY_SNIFF_BYTES)
    except OSError:
        return False
    return b"\x00" in chunk


def _read_text_capped(path: Path, *, max_bytes: int | None = None) -> str:
    """Read the whole file as text; cap bytes if requested."""
    if max_bytes is not None:
        with path.open("rb") as fh:
            raw = fh.read(max_bytes)
        return raw.decode("utf-8", errors="replace")
    return path.read_text(encoding="utf-8", errors="replace")


def fs_read_range(args: dict[str, Any]) -> Any:
    ws, p = _resolve_file(args.get("workspace_id"), args.get("path"), "fs.read_range")
    if _is_binary(p):
        fail(
            code="binary_file",
            message=f"file looks binary: {p.name!r}",
            tool="fs.read_range",
            audit_id="pending",
            run_id="pending",
            suggestion="use workspace.search_text or grep_files for binary",
            workspace_id=ws.id,
        )
    try:
        start_line = int(args.get("start_line") or 0)
        end_line = int(args.get("end_line") or 0)
    except (TypeError, ValueError):
        fail(
            code="invalid_args",
            message="`start_line` and `end_line` must be integers",
            tool="fs.read_range",
            audit_id="pending",
            run_id="pending",
            workspace_id=ws.id,
        )
    if start_line < 0 or end_line < 0:
        fail(
            code="invalid_args",
            message="line numbers must be >= 0",
            tool="fs.read_range",
            audit_id="pending",
            run_id="pending",
            workspace_id=ws.id,
        )
    text = _read_text_capped(p, max_bytes=_MAX_FILE_BYTES)
    all_lines = text.splitlines()
    if end_line <= start_line:
        lines: list[str] = []
    else:
        lines = all_lines[start_line:end_line]
    return {
        "lines": lines,
        "total_lines": len(all_lines),
        "path": p.name,
    }


def fs_tail_log_file(args: dict[str, Any]) -> Any:
    ws, p = _resolve_file(args.get("workspace_id"), args.get("path"), "fs.tail_log_file")
    if _is_binary(p):
        fail(
            code="binary_file",
            message=f"file looks binary: {p.name!r}",
            tool="fs.tail_log_file",
            audit_id="pending",
            run_id="pending",
            suggestion="use workspace.search_text or grep_files for binary",
            workspace_id=ws.id,
        )
    try:
        n = int(args.get("n") or 200)
    except (TypeError, ValueError):
        n = 200
    if n < 1:
        n = 1

    size = p.stat().st_size
    if size > _MAX_FILE_BYTES:
        # Stream the tail: read in chunks from the end.
        chunk = 32 * 1024
        pos = size
        buf = b""
        with p.open("rb") as fh:
            target_newlines = n + 1  # account for partial first line
            while pos > 0 and buf.count(b"\n") < target_newlines:
                read_size = min(chunk, pos)
                pos -= read_size
                fh.seek(pos)
                buf = fh.read(read_size) + buf
                if pos == 0:
                    break
        text = buf.decode("utf-8", errors="replace")
        lines_all = text.splitlines()
        # First line may be partial (started mid-line) — drop it.
        if pos != 0:
            lines_all = lines_all[1:]
        lines = lines_all[-n:]
        truncated = True
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()[-n:]
        truncated = False

    return {
        "lines": lines,
        "path": p.name,
        "truncated": truncated,
    }


def fs_grep_files(args: dict[str, Any]) -> Any:
    ws = _resolve_ws(args.get("workspace_id"), "fs.grep_files")
    pattern = args.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        fail(
            code="invalid_args",
            message="`pattern` must be a non-empty regex",
            tool="fs.grep_files",
            audit_id="pending",
            run_id="pending",
            suggestion="Python re syntax only",
            workspace_id=ws.id,
        )
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        fail(
            code="invalid_args",
            message=f"invalid regex: {exc}",
            tool="fs.grep_files",
            audit_id="pending",
            run_id="pending",
            workspace_id=ws.id,
        )
    try:
        max_results = int(args.get("max_results") or 200)
    except (TypeError, ValueError):
        max_results = 200
    if max_results < 1:
        max_results = 200

    include_glob = args.get("include_glob")
    root = Path(ws.canonical_root)
    matches: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    truncated = False

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS]
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
            if size > _MAX_FILE_BYTES:
                skipped.append({"file": str(fpath.relative_to(root)), "reason": "too_large"})
                continue
            try:
                with fpath.open("rb") as fh:
                    raw = fh.read(_BINARY_SNIFF_BYTES)
                if b"\x00" in raw:
                    skipped.append({"file": str(fpath.relative_to(root)), "reason": "binary"})
                    continue
                text = raw.decode("utf-8", errors="replace")
                if size > _BINARY_SNIFF_BYTES:
                    with fpath.open("r", encoding="utf-8", errors="replace") as fh:
                        fh.seek(_BINARY_SNIFF_BYTES)
                        text += fh.read()
            except OSError:
                skipped.append({"file": str(fpath.relative_to(root)), "reason": "unreadable"})
                continue

            for line_no, line in enumerate(text.splitlines()):
                if rx.search(line):
                    matches.append({
                        "file": str(fpath.relative_to(root)),
                        "line": line_no,
                        "text": line,
                    })
                    if len(matches) >= max_results:
                        truncated = True
                        break
            if truncated:
                break
        if truncated:
            break

    next_actions: list[str] = []
    if truncated:
        next_actions.append("narrow pattern (max_results hit)")
    if any(s["reason"] == "too_large" for s in skipped):
        next_actions.append("some files were skipped because they exceed 5 MiB")

    return {
        "matches": matches,
        "skipped": skipped,
        "truncated": truncated,
        "next_actions": next_actions,
    }


def _glob_match(name: str, pattern: str) -> bool:
    rx = re.escape(pattern).replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
    return re.fullmatch(rx, name) is not None


__all__ = [
    "fs_read_range",
    "fs_tail_log_file",
    "fs_grep_files",
]