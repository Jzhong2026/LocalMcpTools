"""``runtime.*`` tools — runtime detection, environment, PATH introspection.

Three tools:

- :func:`runtime_detect_runtime` — list every Python / Node / dotnet /
  npm executable reachable on ``%PATH%`` (and per-PATH entry). Each entry
  reports ``{version, path, is_default}``. ``is_default`` is True for
  whichever entry resolves first on PATH; subsequent duplicates are
  marked False. The response also includes ``missing`` — runtimes that
  the calling workspace's project type expects but were not found.

- :func:`runtime_get_env` — read one environment variable. Returns
  ``{name, value, source}`` where ``source`` is one of
  ``"process" | "user" | "system" | "missing"``. Values pass through
  :func:`safety.redact.redact` before returning, so a leak in any
  secret-shaped variable still has the same protection as every other
  tool boundary.

- :func:`runtime_list_path` — list each directory on PATH. Each entry
  reports whether it exists, whether the path is a file vs directory,
  and whether it contains a runtime executable that matches the
  project's project type.

All three are read-only and require only the ``observe`` profile, so
they pass :func:`policy.authorize.check` by default. Cache lifetime is
60s for ``detect_runtime`` per the OpenSpec REQ-DIAG-1 contract;
``get_env`` and ``list_path`` are not cached (they're already cheap).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from ..config.settings import load_settings
from ..safety.redact import redact
from ..workspaces.inspect import expected_runtimes_for

# Per spec: cache for 60s. ``threading.Lock`` keeps the cache safe in
# the multi-call case (HTTP shared mode dispatches tool calls in
# different threads).
_CACHE_TTL_S = 60.0
_runtime_cache: dict[str, Any] = {"value": None, "at": 0.0}
_cache_lock = threading.Lock()


# --- Runtime detection ----------------------------------------------------


_RUNTIME_BINS: tuple[str, ...] = ("python", "node", "dotnet", "npm")


def _version_for(binary: str, path: str) -> str | None:
    """Run ``binary --version`` and return the first non-empty line.

    Each of python / node / npm / dotnet prints a useful version on
    ``--version``; the first line is stable and parseable. We let the
    subprocess run for at most 5s — anything slower is treated as
    "unknown version" rather than wedging the MCP loop.
    """
    try:
        proc = subprocess.run(
            [path, "--version"],
            capture_output=True,
            timeout=5.0,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    body = (proc.stdout or "") + (proc.stderr or "")
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _walk_path_for(binary: str) -> list[dict[str, Any]]:
    """Walk each PATH entry and resolve every copy of ``binary``.

    Returns ``[{path, is_default}]``. ``is_default`` is True for the
    *first* hit — the entry that ``shutil.which`` would have returned —
    and False for any duplicates. This matches the OpenSpec
    REQ-DIAG-1 contract.
    """
    path_env = os.environ.get("PATH") or os.environ.get("Path") or ""
    separator = ";" if os.name == "nt" else ":"
    seen: dict[str, dict[str, Any]] = {}
    default = shutil.which(binary)
    for entry in path_env.split(separator):
        entry = entry.strip().strip('"')
        if not entry:
            continue
        candidate = Path(entry) / (binary + (".exe" if os.name == "nt" else ""))
        if not candidate.is_file():
            candidate = Path(entry) / binary
        if not candidate.is_file():
            continue
        resolved = str(candidate.resolve())
        if resolved in seen:
            continue
        seen[resolved] = {
            "version": _version_for(binary, resolved),
            "path": resolved,
            "is_default": resolved.lower() == (default or "").lower(),
        }
    return list(seen.values())


def _detect_runtimes() -> dict[str, Any]:
    """Build the full REQ-DIAG-1 payload (no workspace context)."""
    payload: dict[str, Any] = {}
    for binary in _RUNTIME_BINS:
        payload[binary] = _walk_path_for(binary)
    payload["missing"] = sorted(
        name for name in _RUNTIME_BINS if not payload.get(name)
    )
    return payload


def _detect_runtimes_with_workspace(workspace_id: str | None) -> dict[str, Any]:
    """Detect runtimes plus workspace-aware ``missing``."""
    payload = _detect_runtimes()
    if workspace_id:
        try:
            # Re-use the inspect module's project-type / preset knowledge
            # so the ``missing`` list reflects what *this* workspace needs,
            # not just what's absent globally.
            from ..workspaces.registry import resolve  # local import — avoid cycle
            workspace = resolve(workspace_id)
            root = Path(workspace.canonical_root)
            from ..workspaces.inspect import detect_project_type, detect_presets
            project_type = detect_project_type(root)
            presets = detect_presets(project_type, root)
            expected = expected_runtimes_for(project_type, presets)
            found = {name for name in expected if payload.get(name)}
            payload["missing"] = sorted(set(expected) - found)
        except Exception:  # noqa: BLE001 — workspace is a hint, not a hard dep
            pass
    return payload


# --- env ------------------------------------------------------------------


def _source_for(name: str) -> str:
    """Best-effort ``process`` / ``user`` / ``system`` classifier.

    Real source provenance requires platform-specific APIs (Win32
    registry / WMI for the user/system split). We don't depend on those
    — every variable we can see in ``os.environ`` at all is, by
    definition, in the process environment. ``missing`` is the only
    non-process source we need to surface here.
    """
    return "process" if name in os.environ else "missing"


# --- list_path ------------------------------------------------------------


def _runtime_bin_for_project(project_type: str) -> tuple[str, ...]:
    if project_type in ("python", "mixed"):
        return ("python.exe", "python")
    if project_type in ("node", "mixed"):
        return ("node.exe", "node")
    if project_type in ("dotnet", "mixed"):
        return ("dotnet.exe", "dotnet")
    return ()


# --- Tool bodies ----------------------------------------------------------


def runtime_detect_runtime(args: dict[str, Any]) -> Any:
    """Tool body for ``runtime.detect_runtime``.

    Accepts an optional ``workspace_id`` so the ``missing`` list can
    be project-aware. Cached for 60s per the OpenSpec REQ-DIAG-1.
    """
    workspace_id = args.get("workspace_id")
    if workspace_id is not None and not isinstance(workspace_id, str):
        return {
            "error": {"code": "invalid_args", "message": "workspace_id must be a string"},
        }
    cache_key = workspace_id or "*"

    with _cache_lock:
        cached = _runtime_cache.get(cache_key)
        now = time.monotonic()
        if cached is not None and now - cached["at"] < _CACHE_TTL_S:
            return cached["value"]

    payload = _detect_runtimes_with_workspace(
        workspace_id if isinstance(workspace_id, str) else None
    )
    with _cache_lock:
        _runtime_cache[cache_key] = {"value": payload, "at": time.monotonic()}
    return payload


def runtime_get_env(args: dict[str, Any]) -> Any:
    """Tool body for ``runtime.get_env``."""
    name = args.get("name")
    if not isinstance(name, str) or not name:
        return {
            "error": {
                "code": "invalid_args",
                "message": "`name` must be a non-empty string",
            }
        }
    raw = os.environ.get(name)
    if raw is None:
        return {"name": name, "value": None, "source": "missing"}
    redacted, _ = redact(raw)
    return {"name": name, "value": redacted, "source": _source_for(name)}


def runtime_list_path(args: dict[str, Any]) -> Any:
    """Tool body for ``runtime.list_path``."""
    workspace_id = args.get("workspace_id")
    project_type: str | None = None
    if isinstance(workspace_id, str):
        try:
            from ..workspaces.registry import resolve
            root = Path(resolve(workspace_id).canonical_root)
            from ..workspaces.inspect import detect_project_type
            project_type = detect_project_type(root)
        except Exception:  # noqa: BLE001 — workspace is a hint, not a hard dep
            project_type = None
    path_env = os.environ.get("PATH") or os.environ.get("Path") or ""
    separator = ";" if os.name == "nt" else ":"
    binaries = _runtime_bin_for_project(project_type) if project_type else ()
    entries: list[dict[str, Any]] = []
    for entry in path_env.split(separator):
        entry = entry.strip().strip('"')
        if not entry:
            continue
        path = Path(entry)
        is_file = path.is_file()
        is_dir = path.is_dir()
        executable: str | None = None
        if is_dir:
            for binary in binaries:
                candidate = path / binary
                if candidate.is_file():
                    executable = binary
                    break
        entries.append(
            {
                "dir": entry,
                "exists": is_dir or is_file,
                "is_file": is_file,
                "executable": executable,
            }
        )
    return {"entries": entries}


__all__ = [
    "runtime_detect_runtime",
    "runtime_get_env",
    "runtime_list_path",
]


# Silence "imported but unused" lints for settings (used indirectly in
# tests via monkeypatch).
_ = load_settings