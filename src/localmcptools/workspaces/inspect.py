"""Workspace introspection — project type, Git, presets, runtimes.

Pure read-only. Implements the core of :func:`workspace.inspect`.

Project-type detection
-----------------------

We look for marker files (not their contents — that's expensive and
not what an agent wants). The first hit wins:

- ``package.json``           → ``node``
- ``pyproject.toml``         → ``python``
- ``*.csproj`` / ``*.sln``   → ``dotnet``
- multiple markers          → ``mixed``
- none                      → ``unknown``

Git status
----------

``git status --porcelain`` returns a stable, machine-friendly format.
Empty output = clean. Non-empty = dirty. Missing repo = ``not_a_repo``.
We deliberately **don't** use any third-party Git library; ``subprocess``
keeps us close to the canonical behaviour.

Runtimes
--------

``shutil.which`` + a 5s ``--version`` probe. Names match the matrix in
REQ-WS-2: ``python``, ``node``, ``dotnet``, ``npm``.

Missing runtimes
----------------

Cross-referenced against ``presets_available``. If a project needs
``python`` but ``python`` is missing, that goes into
``missing_runtimes``.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


# --- Marker detection -----------------------------------------------------


def detect_project_type(root: Path) -> str:
    """Return one of: ``node``, ``python``, ``dotnet``, ``mixed``, ``unknown``.

    Detection is purely marker-based: file *existence* at root. We do
    not parse ``package.json`` or ``pyproject.toml``; that belongs in
    later tools (preset / dependency introspection).
    """
    markers: list[str] = []
    if (root / "package.json").exists():
        markers.append("node")
    if (root / "pyproject.toml").exists():
        markers.append("python")
    if any(root.glob("*.csproj")) or any(root.glob("*.sln")):
        markers.append("dotnet")
    if not markers:
        return "unknown"
    if len(markers) == 1:
        return markers[0]
    return "mixed"


# --- Git status -----------------------------------------------------------


def git_status(root: Path) -> dict[str, str | None]:
    """Return ``{status, head, branch}``.

    - ``status``: ``clean`` / ``dirty`` / ``not_a_repo``
    - ``head``: short SHA, or ``None``
    - ``branch``: branch name, or ``None``
    """
    # Cheap repo check first; avoids a noisy stderr if .git is absent.
    if not (root / ".git").exists():
        return {"status": "not_a_repo", "head": None, "branch": None}
    try:
        porcelain = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            timeout=5.0,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.debug("git status failed: %s", exc)
        return {"status": "not_a_repo", "head": None, "branch": None}
    if porcelain.returncode != 0:
        return {"status": "not_a_repo", "head": None, "branch": None}
    status = "clean" if not porcelain.stdout.strip() else "dirty"

    head: str | None = None
    branch: str | None = None
    try:
        rev = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            timeout=5.0,
            text=True,
        )
        if rev.returncode == 0:
            head = rev.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        br = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            timeout=5.0,
            text=True,
        )
        if br.returncode == 0:
            branch = br.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        pass

    return {"status": status, "head": head, "branch": branch}


# --- Preset detection -----------------------------------------------------


# Heuristics only; the real preset table arrives in change-4.
_PRESET_MARKERS: dict[str, list[str]] = {
    "test": [
        # Python
        "pytest.ini", "tests/", "test_*.py", "*_test.py", "pyproject.toml",
        # Node
        "package.json",  # has scripts.test
        # Dotnet
        "*.csproj",  # dotnet test
    ],
    "build": [
        "package.json",  # scripts.build
        "pyproject.toml",
        "*.csproj",
        "Makefile",
    ],
    "lint": [
        ".eslintrc*", "eslint.config.*", ".flake8", "ruff.toml",
        "pyproject.toml", ".editorconfig",
    ],
    "dev_server": [
        "package.json",  # scripts.dev / scripts.start
        "pyproject.toml",
    ],
}


def detect_presets(project_type: str, root: Path) -> list[str]:
    """Return the subset of ``[test, build, lint, dev_server]`` available.

    The test/build/dev_server checks depend on ``project_type`` only
    loosely (any project might have a Makefile); lint is independent
    of project type.

    We don't parse the marker files — that's reserved for change-4
    (managed-process-and-ports) where the real preset engine lives.
    This is the lightweight version an agent needs to make a
    go/no-go decision.
    """
    presets: list[str] = []
    for preset, markers in _PRESET_MARKERS.items():
        for marker in markers:
            # Glob-style markers
            if "*" in marker or "?" in marker:
                if any(root.glob(marker)):
                    presets.append(preset)
                    break
            # Path markers (with trailing slash = must be dir)
            if marker.endswith("/"):
                if (root / marker.rstrip("/")).is_dir():
                    presets.append(preset)
                    break
            # Plain file markers
            if (root / marker).exists():
                presets.append(preset)
                break
    return presets


# --- Runtime detection ----------------------------------------------------


def _probe_version(cmd: list[str], *, timeout: float = 5.0) -> str | None:
    """Run ``cmd --version`` and return the first non-empty line."""
    try:
        proc = subprocess.run(
            [*cmd, "--version"],
            capture_output=True,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    body = (proc.stdout or "") + (proc.stderr or "")
    for line in body.splitlines():
        line = line.strip()
        if line:
            return line
    return None


def detect_runtimes() -> list[dict[str, str | None]]:
    """Probe ``python``, ``node``, ``npm``, ``dotnet`` on PATH.

    Each entry is ``{name, version, path}``. Missing tools are
    *excluded* — callers decide what "missing" means by comparing
    against an expected list.
    """
    candidates: list[tuple[str, list[str]]] = [
        ("python", ["python"]),
        ("node", ["node"]),
        ("npm", ["npm"]),
        ("dotnet", ["dotnet"]),
    ]
    found: list[dict[str, str | None]] = []
    for name, cmd in candidates:
        path = shutil.which(cmd[0])
        if not path:
            continue
        version = _probe_version(cmd)
        found.append({"name": name, "version": version, "path": path})
    return found


def expected_runtimes_for(project_type: str, presets: list[str]) -> list[str]:
    """List the runtimes a project type typically requires.

    Used to compute ``missing_runtimes``.
    """
    expected: set[str] = set()
    if project_type in ("python", "mixed"):
        expected.add("python")
    if project_type in ("node", "mixed"):
        expected.add("node")
        expected.add("npm")
    if project_type in ("dotnet", "mixed"):
        expected.add("dotnet")
    # A Makefile-only project still benefits from python (for tooling)
    if "build" in presets and not expected:
        expected.add("python")
    return sorted(expected)


# --- Top-level inspection --------------------------------------------------


def inspect_workspace(root: Path) -> dict[str, Any]:
    """Return the full REQ-WS-2 payload."""
    project_type = detect_project_type(root)
    git = git_status(root)
    presets = detect_presets(project_type, root)
    runtimes = detect_runtimes()
    found_names = {r["name"] for r in runtimes if r["name"]}
    expected = expected_runtimes_for(project_type, presets)
    missing = [n for n in expected if n not in found_names]
    return {
        "project_type": project_type,
        "git": git,
        "presets_available": presets,
        "runtimes": runtimes,
        "missing_runtimes": missing,
    }


__all__ = [
    "detect_project_type",
    "detect_presets",
    "detect_runtimes",
    "expected_runtimes_for",
    "git_status",
    "inspect_workspace",
]