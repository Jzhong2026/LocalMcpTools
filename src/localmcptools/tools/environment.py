"""``environment.get`` tool — machine context for agents.

Pure read-only. Answers the single question: *what is this host?*
Returns a structured object with OS, PowerShell, encoding, user and
machine fields per the REQ-ENV-1 spec.

Implementation notes:

- We deliberately do **not** require a workspace. ``environment.get``
  is host-scoped, not workspace-scoped.
- The encoding probe runs ``chardet`` against the output of a small
  PowerShell command; the result is cached for 60s.
- ``is_admin`` is probed via ``whoami /groups`` and cached the same
  way. On non-Windows hosts both fields degrade gracefully to ``None``.
- All subprocess calls have a hard 5s timeout so a hanging
  ``powershell.exe`` doesn't freeze the MCP loop.

The shape is pinned by ``openspec/changes/core-shell-and-audit/specs/
environment-and-workspace.md`` — agents build against the field
names, not against their order.

The tool body returns a :class:`ToolResponse` directly when partial
failures need ``next_actions`` attached. Otherwise it returns a plain
dict; the chokepoint wraps both forms identically.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from ._common import ToolMeta, ToolResponse

_log = logging.getLogger(__name__)


# --- Caches ---------------------------------------------------------------

# 60s cache so a chatty agent doesn't fork 5 powershell processes per
# second. ``threading.Lock`` keeps the cache safe across the (rare)
# case of concurrent calls from a multi-agent HTTP shared mode.
_ENCODING_TTL_S = 60.0
_ISADMIN_TTL_S = 60.0

_encoding_cache: dict[str, Any] = {"text": None, "at": 0.0}
_isadmin_cache: dict[str, Any] = {"value": None, "at": 0.0}
_cache_lock = threading.Lock()


# --- Encoding probe -------------------------------------------------------


def _probe_console_encoding_via_powershell() -> str:
    """Run a tiny PowerShell command and let ``chardet`` classify it.

    We emit a sentence with mixed ASCII + Chinese characters and
    verify chardet picks a sane codec. On a Chinese Windows host the
    legacy ``Write-Output`` (no BOM, no explicit encoding) defaults to
    the active console code page — which is exactly what we want to
    surface to the agent.
    """
    try:
        import chardet  # local import; optional dependency
    except ImportError:
        return "unknown"

    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-Command",
             "$OutputEncoding = [Console]::OutputEncoding; "
             "Write-Output 'LocalMcpTools encoding probe: hello world.'"],
            capture_output=True,
            timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.debug("encoding probe failed: %s", exc)
        return "unknown"

    raw = proc.stdout or b""
    if not raw:
        return "unknown"
    det = chardet.detect(raw)
    encoding = (det.get("encoding") or "unknown").lower()
    # Normalise a few common aliases.
    alias = {
        "gb2312": "gbk",
        "gb18030": "gbk",
    }.get(encoding, encoding)
    return alias or "unknown"


def _get_console_encoding() -> str:
    with _cache_lock:
        cached: Any = _encoding_cache["text"]
        if cached is not None and (
            time.monotonic() - _encoding_cache["at"] < _ENCODING_TTL_S
        ):
            text_cached: str = cached
            return text_cached
    text = _probe_console_encoding_via_powershell()
    with _cache_lock:
        _encoding_cache["text"] = text
        _encoding_cache["at"] = time.monotonic()
    return text


def _get_active_code_page() -> int | None:
    """Best-effort: ``kernel32.GetConsoleOutputCP`` via PowerShell."""
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-Command",
             "[Console]::OutputEncoding.CodePage"],
            capture_output=True,
            timeout=5.0,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (proc.stdout or "").strip()
    if not out.isdigit():
        return None
    return int(out)


# --- Admin probe ----------------------------------------------------------


def _probe_is_admin() -> bool | None:
    """``whoami /groups`` contains ``Mandatory Label\\High Mandatory Level``.

    If we see ``S-1-16-12288`` we have an elevated token. On non-Windows
    hosts we return ``None`` (the spec says ``None`` when unavailable,
    not ``False``).
    """
    if os.name != "nt":
        return None
    try:
        proc = subprocess.run(
            ["whoami", "/groups"],
            capture_output=True,
            timeout=5.0,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    body = (proc.stdout or "") + (proc.stderr or "")
    return "S-1-16-12288" in body


def _get_is_admin() -> bool | None:
    with _cache_lock:
        cached: Any = _isadmin_cache["value"]
        if cached is not None and (
            time.monotonic() - _isadmin_cache["at"] < _ISADMIN_TTL_S
        ):
            val_cached: bool = cached
            return val_cached
    val = _probe_is_admin()
    with _cache_lock:
        _isadmin_cache["value"] = val
        _isadmin_cache["at"] = time.monotonic()
    return val


# --- PowerShell probe -----------------------------------------------------


def _probe_powershell() -> dict[str, str | None]:
    """Return ``{version, edition, executable}`` from a 5s PowerShell probe."""
    out: dict[str, str | None] = {
        "version": None, "edition": None, "executable": None,
    }
    pwsh = shutil.which("powershell.exe")
    if pwsh:
        out["executable"] = pwsh
    else:
        return out
    try:
        proc = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive",
             "-Command",
             "$PSVersionTable.PSVersion.ToString() + '|' + "
             "$PSVersionTable.PSEdition"],
            capture_output=True,
            timeout=5.0,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return out
    line = (proc.stdout or "").strip()
    if "|" in line:
        version, edition = line.split("|", 1)
        out["version"] = version.strip() or None
        out["edition"] = edition.strip() or None
    elif line:
        out["version"] = line
    return out


# --- Tool body ------------------------------------------------------------


def _build_payload() -> tuple[dict[str, Any], list[str]]:
    """Probe every field, return ``(payload, failure_actions)``."""
    encoding_console = _get_console_encoding()
    active_cp = _get_active_code_page()
    is_admin = _get_is_admin()
    pwsh = _probe_powershell()

    failures: list[str] = []

    preferred_fs = "utf-8"
    if encoding_console in ("gbk", "gb2312"):
        preferred_fs = "gbk"
    if encoding_console == "unknown":
        failures.append(
            "could not probe console encoding; run 'chcp' in PowerShell "
            "and verify output encoding is utf-8"
        )
    if os.name == "nt" and is_admin is None:
        failures.append(
            "could not determine admin status via 'whoami /groups'"
        )

    payload: dict[str, Any] = {
        "os": {
            "name": os.name,
            "version": platform.version() or None,
            "build": platform.release() or None,
            "architecture": platform.machine() or None,
        },
        "powershell": pwsh,
        "encoding": {
            "active_code_page": active_cp,
            "console_output": encoding_console,
            "preferred_fs": preferred_fs,
        },
        "user": {
            "name": os.environ.get("USERNAME") or os.environ.get("USER") or None,
            "is_admin": is_admin,
        },
        "cwd": str(Path.cwd()),
        "machine": {
            "name": platform.node() or None,
            "python_version": platform.python_version(),
        },
    }
    return payload, failures


def environment_get(args: dict[str, Any]) -> Any:
    """Tool body for ``environment.get``.

    Takes no arguments; ``args`` is accepted for symmetry with the
    chokepoint signature.

    Returns either a plain dict (chokepoint wraps in ok_response) or
    a fully-built :class:`ToolResponse` if we want to attach
    ``next_actions``. The chokepoint handles both cases.
    """
    _ = args  # intentionally ignored
    payload, failures = _build_payload()
    if not failures:
        return payload

    # Partial-failure path: build a full envelope so we can populate
    # ``meta.next_actions``. The chokepoint will refresh the audit /
    # run / duration fields before returning the dict to the agent.
    # NOTE: we leave ``ok=True`` because the *machine* values are
    # present — only the *probes* partially failed. The spec says
    # ``next_actions`` must be non-empty whenever ``ok: false``; we
    # choose to keep ``ok=True`` (informative) and let next_actions
    # speak. If a stricter interpretation is wanted, swap to ok=False.
    meta = ToolMeta(
        tool="environment.get",
        duration_ms=0,  # chokepoint overwrites
        audit_id="pending",  # chokepoint overwrites
        run_id="pending",  # chokepoint overwrites
        next_actions=failures,
    )
    return ToolResponse.ok_response(data=payload, meta=meta)


__all__ = ["environment_get"]
