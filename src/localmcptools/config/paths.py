"""Filesystem paths for LocalMcpTools runtime state.

Resolution order (first wins):

1. ``LMCP_DATA_DIR`` environment variable — used by tests and portable installs.
2. ``%APPDATA%\\LocalMcpTools`` on Windows (``os.environ['APPDATA']``).
3. ``~/.localmcptools`` on other platforms (Linux/macOS stub — interfaces only).

The chosen directory is created on first access; subsequent calls return the
same path without side effects. We deliberately *do not* touch the directory
at import time — every function that needs the path calls :func:`data_dir`
or :func:`audit_db_path`, which lazily materialises the folder.
"""

from __future__ import annotations

import os
from pathlib import Path

# Environment variable used to override the data directory (tests, portable installs).
ENV_OVERRIDE = "LMCP_DATA_DIR"

# Default app-data subfolder on Windows.
_APP_DIR_NAME = "LocalMcpTools"

# Fallback for non-Windows platforms (interface-only stub per openspec).
_FALLBACK_DIR_NAME = ".localmcptools"


def data_dir() -> Path:
    """Return the root directory for LocalMcpTools runtime state.

    Creates the directory if it does not exist. Pure function over the
    environment — repeated calls return the same path and do not race
    on Windows because ``mkdir(exist_ok=True)`` is atomic at the OS level
    for the immediate parent.
    """
    p = _resolve_data_dir()
    p.mkdir(parents=True, exist_ok=True)
    return p


def audit_db_path() -> Path:
    """Path to the SQLite audit database.

    The file itself is *not* created here — :mod:`localmcptools.persistence.db`
    owns creation so that tests can monkeypatch this without touching disk.
    """
    return data_dir() / "audit.sqlite"


def config_path() -> Path:
    """Path to the optional ``config.json`` file.

    Missing file is normal — :mod:`.settings` falls back to defaults.
    """
    return data_dir() / "config.json"


def server_json_path() -> Path:
    """Path to the long-running server metadata file (change-4 only).

    Not written during the bootstrap spike (stdio mode has no listener).
    """
    return data_dir() / "server.json"


def logs_dir() -> Path:
    """Directory where per-tool execution logs are written."""
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _resolve_data_dir() -> Path:
    override = os.environ.get(ENV_OVERRIDE)
    if override:
        return Path(override)

    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / _APP_DIR_NAME
        # APPDATA missing on Windows is a misconfiguration; fall back
        # to user home so we never crash on import.
        return Path.home() / _APP_DIR_NAME

    # Non-Windows stub — kept here so tests on Linux/macOS still work.
    return Path.home() / _FALLBACK_DIR_NAME