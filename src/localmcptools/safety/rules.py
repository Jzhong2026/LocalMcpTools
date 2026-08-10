"""Deny-only command rules loaded from built-in and operator directories."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..persistence import db
from .redact import redact


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    suggestion: str
    clauses: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class RuleMatch:
    rule_id: str
    severity: str
    suggestion: str


class RuleEngine:
    """Atomically replaceable deny-rule set; malformed files are reported."""

    def __init__(self, *, builtin_dir: Path | None = None, custom_dir: Path | None = None) -> None:
        self._builtin_dir = builtin_dir or Path(__file__).with_name("builtin")
        self._custom_dir = custom_dir
        self._rules: tuple[Rule, ...] = ()
        self._disabled: frozenset[str] = frozenset()

    def reload(self) -> dict[str, Any]:
        loaded, errors = load_all(self._builtin_dir, self._custom_dir)
        self._rules = tuple(loaded)
        # Drop any disabled ids that no longer exist after reload.
        existing_ids = {rule.id for rule in self._rules}
        self._disabled = frozenset(self._disabled & existing_ids)
        return {"reloaded": len(loaded), "errors": errors}

    def match(self, cmd: str, args: list[str] | None = None) -> RuleMatch | None:
        command = " ".join([cmd, *(args or [])])
        for rule in self._rules:
            if rule.id in self._disabled:
                continue
            if any(_matches_clause(command, clause) for clause in rule.clauses):
                return RuleMatch(rule.id, rule.severity, rule.suggestion)
        return None

    def set_enabled(self, rule_id: str, enabled: bool) -> bool:
        """Toggle a rule on/off. Returns True if the id was known."""
        if not any(rule.id == rule_id for rule in self._rules):
            return False
        if enabled:
            self._disabled = self._disabled - {rule_id}
        else:
            self._disabled = self._disabled | {rule_id}
        return True

    @property
    def disabled_ids(self) -> frozenset[str]:
        """Currently disabled rule ids (read-only snapshot)."""
        return self._disabled


def load_all(builtin_dir: Path, custom_dir: Path | None = None) -> tuple[list[Rule], list[dict[str, str]]]:
    """Load valid rule JSON, returning errors rather than disabling protection."""
    rules: list[Rule] = []
    errors: list[dict[str, str]] = []
    directories = [builtin_dir]
    if custom_dir is not None:
        directories.append(custom_dir)
    for directory in directories:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                rules.append(_parse_rule(path))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                errors.append({"file": str(path), "message": str(exc)})
    return rules, errors


def record_hit(rule_id: str, cmd: str, *, conn: sqlite3.Connection | None = None) -> None:
    """Increment per-rule telemetry, retaining only a safe bounded command sample."""
    now = int(time.time() * 1000)
    sample, _ = redact(cmd[:200])
    sql = (
        "INSERT INTO rule_hit_stats (rule_id, hit_count, last_hit_at, last_hit_cmd) VALUES (?, 1, ?, ?) "
        "ON CONFLICT(rule_id) DO UPDATE SET hit_count = hit_count + 1, "
        "last_hit_at = excluded.last_hit_at, last_hit_cmd = excluded.last_hit_cmd"
    )
    if conn is not None:
        conn.execute(sql, (rule_id, now, sample))
        return
    db.init_db()
    with db.connection() as connection:
        connection.execute(sql, (rule_id, now, sample))


def _parse_rule(path: Path) -> Rule:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("rule must be a JSON object")
    identifier = raw.get("id")
    severity = raw.get("severity")
    match = raw.get("match")
    if not isinstance(identifier, str) or not isinstance(severity, str) or not isinstance(match, dict):
        raise ValueError("rule requires string id, string severity and match object")
    clauses = match.get("rules") if match.get("type") == "any_of" else None
    if not isinstance(clauses, list) or not all(isinstance(item, dict) for item in clauses):
        raise ValueError("match must be an any_of list")
    checked: list[dict[str, str]] = []
    for clause in clauses:
        converted = {str(key): str(value) for key, value in clause.items()}
        if not ({"cmd_name"} & set(converted) or {"regex"} & set(converted)):
            raise ValueError("each clause needs cmd_name or regex")
        checked.append(converted)
    return Rule(identifier, severity, str(raw.get("suggestion", "command blocked by safety policy")), tuple(checked))


def _matches_clause(command: str, clause: dict[str, str]) -> bool:
    if "regex" in clause and re.search(clause["regex"], command, flags=re.IGNORECASE):
        return True
    cmd_name = clause.get("cmd_name")
    if cmd_name is None or re.search(rf"(?<![\w.-]){re.escape(cmd_name)}(?![\w.-])", command, re.IGNORECASE) is None:
        return False
    args_match = clause.get("args_match")
    return args_match is None or args_match.lower() in command.lower()


__all__ = ["Rule", "RuleEngine", "RuleMatch", "load_all", "record_hit"]
