"""Semantic command presets for supported workspace types."""

from __future__ import annotations

from pathlib import Path


class NoPreset(LookupError):
    """No safe semantic preset is available for this workspace."""


def resolve(project_type: str, action: str, root: Path, filter_text: str | None = None) -> list[str]:
    """Resolve an action to argv without accepting an agent shell string."""
    table: dict[str, dict[str, list[str]]] = {
        "node": {
            "run_test": ["npm", "test"], "build": ["npm", "run", "build"], "lint": ["npm", "run", "lint"],
        },
        "python": {
            "run_test": ["pytest", "-q"], "build": ["python", "-m", "build"], "lint": ["ruff", "check", "."],
        },
        "dotnet": {
            "run_test": ["dotnet", "test"], "build": ["dotnet", "build"], "lint": ["dotnet", "format", "--verify-no-changes"],
        },
    }
    try:
        command = list(table[project_type][action])
    except KeyError as exc:
        raise NoPreset(f"no {action} preset for project type {project_type!r}") from exc
    if action == "run_test" and filter_text:
        if project_type == "node":
            command.extend(["--", filter_text])
        else:
            command.append(filter_text)
    return command


__all__ = ["NoPreset", "resolve"]
