"""Closed registry of approved long-running development-server commands."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Any


class UnknownPreset(LookupError):
    """A caller named a preset outside the closed registry."""


@dataclass(frozen=True)
class Preset:
    name: str
    command_prefix: tuple[str, ...]
    default_args: tuple[str, ...]
    env_required: tuple[str, ...]
    port_hint_regex: re.Pattern[str]
    project_argument_required: bool = False


_PORT_HINT = re.compile(
    r"(?:https?://(?:127\.0\.0\.1|localhost|0\.0\.0\.0):|(?:port|listening on)\s+)(\d{2,5})",
    re.IGNORECASE,
)

PRESETS: dict[str, Preset] = {
    "python-uvicorn": Preset(
        "python-uvicorn", (sys.executable, "-m", "uvicorn"), ("--reload",), (), _PORT_HINT
    ),
    "node-vite": Preset("node-vite", ("npx", "vite"), (), (), _PORT_HINT),
    "node-next-dev": Preset("node-next-dev", ("npx", "next", "dev"), (), (), _PORT_HINT),
    "dotnet-run": Preset(
        "dotnet-run", ("dotnet", "run", "--project"), (), (), _PORT_HINT, True
    ),
}


def resolve(
    preset_name: str, args: Any, *, workspace_id: str | None = None,
) -> tuple[Preset, tuple[str, ...]]:
    """Validate arguments and resolve a preset to an argv vector."""
    if workspace_id is not None and not workspace_id:
        raise ValueError("workspace_id must not be empty")
    try:
        preset = PRESETS[preset_name]
    except KeyError as exc:
        raise UnknownPreset(f"unknown managed-process preset: {preset_name!r}") from exc
    if args is None:
        values: list[str] = []
    elif isinstance(args, list) and all(isinstance(item, str) for item in args):
        values = list(args)
    else:
        raise ValueError("args must be an array of strings")
    if preset.project_argument_required and not values:
        raise ValueError("dotnet-run requires the project path as args[0]")
    if any("\x00" in item for item in values):
        raise ValueError("args must not contain NUL characters")
    return preset, (*preset.command_prefix, *values, *preset.default_args)


def suggest_for_command(command: str) -> str:
    lowered = command.lower()
    if "uvicorn" in lowered or "python" in lowered:
        return "python-uvicorn"
    if "next" in lowered:
        return "node-next-dev"
    if "vite" in lowered or "npm" in lowered or "npx" in lowered:
        return "node-vite"
    if "dotnet" in lowered:
        return "dotnet-run"
    return "process.start_dev_server"


__all__ = ["PRESETS", "Preset", "UnknownPreset", "resolve", "suggest_for_command"]
